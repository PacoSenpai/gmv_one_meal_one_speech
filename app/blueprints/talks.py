from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for
from marshmallow import ValidationError
from werkzeug.datastructures import FileStorage

from app.extensions import db, limiter
from app.models import Comment, Reaction, Talk, TalkImage, TalkResource, utc_now
from app.rate_limits import rate_limit_principal
from app.schemas import CommentSchema, ProposalSchema
from app.security import roles_required
from app.services.scheduling import is_evaluation_open, next_default_slot, occupied_default_slots
from app.services.uploads import InvalidImageError, save_uploaded_image

talks_bp = Blueprint("talks", __name__)


def _parse_resources(raw_value: str) -> list[tuple[str, str]]:
    parsed_resources: list[tuple[str, str]] = []
    for line_number, line in enumerate(raw_value.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        if "|" in line:
            label, address = (part.strip() for part in line.split("|", maxsplit=1))
        else:
            address = line
            label = urlparse(address).netloc or f"Recurso {line_number}"
        parsed_url = urlparse(address)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc or parsed_url.username:
            raise ValueError(f"El recurso de la línea {line_number} debe ser un enlace http o https válido.")
        if len(label) > 100 or len(address) > 2048:
            raise ValueError(f"El recurso de la línea {line_number} supera la longitud permitida.")
        parsed_resources.append((label or parsed_url.netloc, address))
    if len(parsed_resources) > 20:
        raise ValueError("Puedes añadir como máximo 20 recursos.")
    return parsed_resources


@talks_bp.route("/proponer", methods=["GET", "POST"])
@roles_required("member", "admin")
@limiter.limit("5 per hour", key_func=rate_limit_principal, methods=["POST"])
def propose():
    if request.method == "GET":
        return render_template("talks/propose.html")

    try:
        payload = ProposalSchema().load(request.form.to_dict())
        resources = _parse_resources(request.form.get("resources", ""))
    except (ValidationError, ValueError) as error:
        errors = error.messages if isinstance(error, ValidationError) else {"resources": [str(error)]}
        return render_template("talks/propose.html", errors=errors, form=request.form), 400

    uploaded_files: list[FileStorage] = [
        item for item in request.files.getlist("images") if item and item.filename
    ]
    if len(uploaded_files) > 5:
        return render_template(
            "talks/propose.html",
            errors={"images": ["Puedes subir como máximo cinco imágenes."]},
            form=request.form,
        ), 400

    filenames: list[str] = []
    try:
        for uploaded_file in uploaded_files:
            filenames.append(save_uploaded_image(uploaded_file))
    except InvalidImageError as error:
        for filename in filenames:
            _remove_uploaded_file(filename)
        return render_template(
            "talks/propose.html",
            errors={"images": [str(error)]},
            form=request.form,
        ), 400

    talk = Talk(
        title=payload["title"].strip(),
        description=payload["description"].strip(),
        event_at=next_default_slot(occupied=occupied_default_slots()),
        speaker_id=g.current_user.id,
    )
    talk.resources = [TalkResource(label=label, url=address) for label, address in resources]
    talk.images = [
        TalkImage(filename=filename, position=position)
        for position, filename in enumerate(filenames)
    ]
    db.session.add(talk)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        for filename in filenames:
            _remove_uploaded_file(filename)
        raise

    flash("Tu exposición ya está en la cola pública.", "success")
    return redirect(url_for("public.talk_detail", talk_id=talk.id))


@talks_bp.get("/mis-charlas")
@roles_required("member", "admin")
@limiter.limit("60 per minute", key_func=rate_limit_principal)
def my_talks():
    user_id = g.current_user.id
    return render_template(
        "talks/my_talks.html",
        scheduled_talks=(
            Talk.query.filter_by(speaker_id=user_id, status="scheduled")
            .order_by(Talk.event_at.asc())
            .all()
        ),
        completed_talks=(
            Talk.query.filter_by(speaker_id=user_id, status="completed")
            .order_by(Talk.completed_at.desc())
            .all()
        ),
        cancelled_talks=(
            Talk.query.filter_by(speaker_id=user_id, status="cancelled")
            .order_by(Talk.event_at.desc())
            .all()
        ),
        now=utc_now(),
    )


@talks_bp.route("/charlas/<int:talk_id>/editar", methods=["GET", "POST"])
@roles_required("member", "admin")
@limiter.limit("10 per hour", key_func=rate_limit_principal, methods=["POST"])
def edit_own_talk(talk_id: int):
    talk = db.get_or_404(Talk, talk_id)
    if talk.speaker_id != g.current_user.id:
        abort(403)
    if not _is_future_scheduled(talk):
        flash("Solo puedes editar tus charlas que aún no se han realizado.", "error")
        return redirect(url_for("public.talk_detail", talk_id=talk.id))
    if request.method == "GET":
        return _render_edit_talk(talk)

    try:
        payload = ProposalSchema().load(request.form.to_dict())
        resources = _parse_resources(request.form.get("resources", ""))
        removed_image_ids = {int(value) for value in request.form.getlist("remove_images")}
    except (ValidationError, ValueError) as error:
        errors = error.messages if isinstance(error, ValidationError) else {"resources": [str(error)]}
        return _render_edit_talk(talk, errors=errors, form=request.form), 400

    image_by_id = {image.id: image for image in talk.images}
    if not removed_image_ids.issubset(image_by_id):
        abort(400)
    retained_images = [image for image in talk.images if image.id not in removed_image_ids]
    uploaded_files: list[FileStorage] = [
        item for item in request.files.getlist("images") if item and item.filename
    ]
    available_slots = current_app.config["MAX_IMAGES_PER_TALK"] - len(retained_images)
    if len(uploaded_files) > available_slots:
        return _render_edit_talk(
            talk,
            errors={"images": ["Puedes conservar o añadir hasta cinco imágenes en total."]},
            form=request.form,
        ), 400

    saved_filenames: list[str] = []
    try:
        for uploaded_file in uploaded_files:
            saved_filenames.append(save_uploaded_image(uploaded_file))
    except InvalidImageError as error:
        for filename in saved_filenames:
            _remove_uploaded_file(filename)
        return _render_edit_talk(
            talk,
            errors={"images": [str(error)]},
            form=request.form,
        ), 400

    removed_filenames = [image_by_id[image_id].filename for image_id in removed_image_ids]
    talk.title = payload["title"].strip()
    talk.description = payload["description"].strip()
    talk.resources = [TalkResource(label=label, url=address) for label, address in resources]
    talk.images = retained_images + [
        TalkImage(filename=filename, position=len(retained_images) + index)
        for index, filename in enumerate(saved_filenames)
    ]
    for position, image in enumerate(talk.images):
        image.position = position

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        for filename in saved_filenames:
            _remove_uploaded_file(filename)
        raise

    for filename in removed_filenames:
        _remove_uploaded_file(filename)
    flash("Tu charla se ha actualizado.", "success")
    return redirect(url_for("public.talk_detail", talk_id=talk.id))


def _render_edit_talk(
    talk: Talk,
    *,
    errors: dict[str, list[str]] | None = None,
    form: object | None = None,
):
    resources_value = "\n".join(f"{item.label} | {item.url}" for item in talk.resources)
    return render_template(
        "talks/edit.html",
        talk=talk,
        resources_value=resources_value,
        errors=errors,
        form=form,
    )


@talks_bp.post("/charlas/<int:talk_id>/eliminar")
@roles_required("member", "admin")
@limiter.limit("5 per hour", key_func=rate_limit_principal)
def delete_own_talk(talk_id: int):
    talk = db.get_or_404(Talk, talk_id)
    if talk.speaker_id != g.current_user.id:
        abort(403)
    if not _is_future_scheduled(talk):
        abort(400)

    image_filenames = [image.filename for image in talk.images]
    following_talks = (
        Talk.query.filter(Talk.status == "scheduled", Talk.event_at > talk.event_at)
        .order_by(Talk.event_at.asc(), Talk.id.asc())
        .all()
    )
    previous_slot = talk.event_at
    for following_talk in following_talks:
        next_slot = following_talk.event_at
        following_talk.event_at = previous_slot
        previous_slot = next_slot

    db.session.delete(talk)
    db.session.commit()
    for filename in image_filenames:
        _remove_uploaded_file(filename)
    if following_talks:
        flash(
            f"La charla se ha eliminado y se han adelantado {len(following_talks)} "
            "charlas para cerrar el hueco.",
            "success",
        )
    else:
        flash("La charla se ha eliminado.", "success")
    return redirect(url_for("talks.my_talks"))


@talks_bp.post("/charlas/<int:talk_id>/ceder-turno")
@roles_required("member", "admin")
@limiter.limit("5 per hour", key_func=rate_limit_principal)
def defer_one_position(talk_id: int):
    talk = db.get_or_404(Talk, talk_id)
    if talk.speaker_id != g.current_user.id:
        abort(403)
    if not _is_future_scheduled(talk):
        abort(400)

    following_talk = _next_talk_after(talk)
    if following_talk is None:
        flash("No hay otra charla programada detrás de la tuya para intercambiar el turno.", "error")
        return redirect(url_for("public.talk_detail", talk_id=talk.id))

    talk.event_at, following_talk.event_at = following_talk.event_at, talk.event_at
    db.session.commit()
    flash(
        f"Turno intercambiado: ahora expondrás después de {following_talk.speaker.display_name}.",
        "success",
    )
    return redirect(url_for("public.talk_detail", talk_id=talk.id))


def _is_future_scheduled(talk: Talk) -> bool:
    return talk.status == "scheduled" and talk.event_at > utc_now()


def _next_talk_after(talk: Talk) -> Talk | None:
    return (
        Talk.query.filter(Talk.status == "scheduled", Talk.event_at > talk.event_at)
        .order_by(Talk.event_at.asc())
        .first()
    )


def _remove_uploaded_file(filename: str) -> None:
    (Path(current_app.config["UPLOAD_FOLDER"]) / filename).unlink(missing_ok=True)


@talks_bp.post("/charlas/<int:talk_id>/valoracion")
@roles_required("member", "admin")
@limiter.limit("30 per minute", key_func=rate_limit_principal)
def react(talk_id: int):
    talk = db.get_or_404(Talk, talk_id)
    if not is_evaluation_open(talk):
        flash("Las valoraciones de esta charla no están abiertas.", "error")
        return redirect(url_for("public.talk_detail", talk_id=talk.id))

    value = request.form.get("value", "")
    if value not in {"like", "dislike"}:
        abort(400)
    reaction = Reaction.query.filter_by(talk_id=talk.id, user_id=g.current_user.id).first()
    if reaction is None:
        reaction = Reaction(talk_id=talk.id, user_id=g.current_user.id, value=value)
        db.session.add(reaction)
    else:
        reaction.value = value
    db.session.commit()
    flash("Tu valoración se ha guardado.", "success")
    return redirect(url_for("public.talk_detail", talk_id=talk.id) + "#valoraciones")


@talks_bp.post("/charlas/<int:talk_id>/comentarios")
@roles_required("member", "admin")
@limiter.limit("10 per minute", key_func=rate_limit_principal)
def add_comment(talk_id: int):
    talk = db.get_or_404(Talk, talk_id)
    if not is_evaluation_open(talk):
        flash("Los comentarios de esta charla no están abiertos.", "error")
        return redirect(url_for("public.talk_detail", talk_id=talk.id))
    try:
        payload = CommentSchema().load(request.form.to_dict())
    except ValidationError as error:
        return render_template(
            "talks/detail.html",
            talk=talk,
            comments=_top_level_comments(talk),
            evaluation_open=True,
            current_reaction=_current_reaction(talk),
            errors=error.messages,
        ), 400

    db.session.add(Comment(body=payload["body"].strip(), talk_id=talk.id, author_id=g.current_user.id))
    db.session.commit()
    return redirect(url_for("public.talk_detail", talk_id=talk.id) + "#comentarios")


@talks_bp.post("/charlas/<int:talk_id>/comentarios/<int:comment_id>/responder")
@roles_required("member", "admin")
@limiter.limit("10 per minute", key_func=rate_limit_principal)
def reply_to_comment(talk_id: int, comment_id: int):
    talk = db.get_or_404(Talk, talk_id)
    comment = db.get_or_404(Comment, comment_id)
    if comment.talk_id != talk.id or comment.parent_id is not None:
        abort(404)
    if talk.speaker_id != g.current_user.id:
        abort(403)
    if not is_evaluation_open(talk):
        flash("El periodo de comentarios ha terminado.", "error")
        return redirect(url_for("public.talk_detail", talk_id=talk.id))
    if comment.replies:
        flash("El ponente ya ha respondido a este comentario.", "error")
        return redirect(url_for("public.talk_detail", talk_id=talk.id) + "#comentarios")

    try:
        payload = CommentSchema().load(request.form.to_dict())
    except ValidationError as error:
        return render_template(
            "talks/detail.html",
            talk=talk,
            comments=_top_level_comments(talk),
            evaluation_open=True,
            current_reaction=_current_reaction(talk),
            errors=error.messages,
        ), 400

    db.session.add(
        Comment(
            body=payload["body"].strip(),
            talk_id=talk.id,
            author_id=g.current_user.id,
            parent_id=comment.id,
        )
    )
    db.session.commit()
    return redirect(url_for("public.talk_detail", talk_id=talk.id) + "#comentarios")


def _top_level_comments(talk: Talk) -> list[Comment]:
    return (
        Comment.query.filter_by(talk_id=talk.id, parent_id=None)
        .order_by(Comment.created_at.asc())
        .all()
    )


def _current_reaction(talk: Talk) -> str | None:
    if not getattr(g, "current_user", None):
        return None
    reaction = Reaction.query.filter_by(talk_id=talk.id, user_id=g.current_user.id).first()
    return reaction.value if reaction else None
