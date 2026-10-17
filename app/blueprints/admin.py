from __future__ import annotations

import secrets
import string
from datetime import date, datetime

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from marshmallow import ValidationError

from app.extensions import db, limiter
from app.models import Comment, Invitation, Talk, User, utc_now
from app.rate_limits import rate_limit_principal
from app.schemas import InvitationSchema, ProposalSchema
from app.security import roles_required
from app.services.dates import app_timezone, local_datetime, parse_local_datetime
from app.services.invitations import create_invitation_code

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.get("/")
@roles_required("admin")
@limiter.limit("60 per minute", key_func=rate_limit_principal)
def dashboard():
    upcoming_talks = (
        Talk.query.filter_by(status="scheduled")
        .order_by(Talk.event_at.asc())
        .all()
    )
    pending_invitations = Invitation.query.filter(
        Invitation.used_at.is_(None), Invitation.expires_at >= utc_now()
    ).count()
    return render_template(
        "admin/dashboard.html",
        upcoming_talks=upcoming_talks,
        members=User.query.filter_by(role="member", is_active=True).count(),
        pending_invitations=pending_invitations,
        total_talks=Talk.query.count(),
    )


@admin_bp.route("/invitaciones", methods=["GET", "POST"])
@roles_required("admin")
@limiter.limit("10 per hour", key_func=rate_limit_principal, methods=["POST"])
def invitations():
    if request.method == "POST":
        try:
            payload = InvitationSchema().load(request.form.to_dict())
        except ValidationError as error:
            return render_template(
                "admin/invitations.html",
                invitations=_invitation_list(),
                now=utc_now(),
                errors=error.messages,
                form=request.form,
            ), 400

        expires_on: date = payload["expires_at"]
        if expires_on < datetime.now(app_timezone()).date():
            return render_template(
                "admin/invitations.html",
                invitations=_invitation_list(),
                now=utc_now(),
                errors={"expires_at": ["La fecha de caducidad debe ser hoy o posterior."]},
                form=request.form,
            ), 400
        invitation, code = create_invitation_code(expires_on, g.current_user.id)
        db.session.add(invitation)
        db.session.commit()
        return render_template("admin/invitation_created.html", code=code, invitation=invitation)

    return render_template("admin/invitations.html", invitations=_invitation_list(), now=utc_now())


def _invitation_list() -> list[Invitation]:
    return Invitation.query.order_by(Invitation.created_at.desc()).limit(50).all()


@admin_bp.post("/invitaciones/<int:invitation_id>/revocar")
@roles_required("admin")
@limiter.limit("20 per minute", key_func=rate_limit_principal)
def revoke_invitation(invitation_id: int):
    invitation = db.get_or_404(Invitation, invitation_id)
    if invitation.used_at is not None:
        flash("Esta invitación ya se utilizó y no se puede revocar.", "error")
        return redirect(url_for("admin.invitations"))
    invitation.expires_at = utc_now()
    db.session.commit()
    flash("Invitación revocada.", "success")
    return redirect(url_for("admin.invitations"))


@admin_bp.get("/miembros")
@roles_required("admin")
@limiter.limit("60 per minute", key_func=rate_limit_principal)
def users():
    return render_template(
        "admin/users.html",
        users=User.query.order_by(User.is_active.desc(), User.username.asc()).all(),
    )


@admin_bp.post("/miembros/<int:user_id>/restablecer-contrasena")
@roles_required("admin")
@limiter.limit("5 per minute", key_func=rate_limit_principal)
def reset_password(user_id: int):
    user = db.get_or_404(User, user_id)
    if not user.is_active:
        abort(400)
    alphabet = string.ascii_letters + string.digits + "!@#$%*+-_"
    temporary_password = "".join(secrets.choice(alphabet) for _ in range(20))
    user.set_password(temporary_password)
    user.must_change_password = True
    user.auth_version += 1
    db.session.commit()
    return render_template(
        "admin/temporary_password.html",
        user=user,
        temporary_password=temporary_password,
    )


@admin_bp.post("/miembros/<int:user_id>/estado")
@roles_required("admin")
@limiter.limit("20 per minute", key_func=rate_limit_principal)
def toggle_user_status(user_id: int):
    user = db.get_or_404(User, user_id)
    requested_status = request.form.get("active") == "1"
    if user.id == g.current_user.id and not requested_status:
        flash("No puedes desactivar tu propia cuenta desde esta sesión.", "error")
        return redirect(url_for("admin.users"))
    if user.role == "admin" and user.is_active and not requested_status:
        active_admin_count = User.query.filter_by(role="admin", is_active=True).count()
        if active_admin_count <= 1:
            flash("Debe quedar al menos un administrador activo.", "error")
            return redirect(url_for("admin.users"))
    user.is_active = requested_status
    user.auth_version += 1
    db.session.commit()
    flash("Estado de la cuenta actualizado.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.post("/miembros/<int:user_id>/rol")
@roles_required("admin")
@limiter.limit("20 per minute", key_func=rate_limit_principal)
def change_user_role(user_id: int):
    user = db.get_or_404(User, user_id)
    role = request.form.get("role", "")
    if role not in {"member", "admin"}:
        abort(400)
    if user.role == "admin" and user.is_active and role != "admin":
        if User.query.filter_by(role="admin", is_active=True).count() <= 1:
            flash("Debe quedar al menos un administrador activo.", "error")
            return redirect(url_for("admin.users"))
    user.role = role
    user.auth_version += 1
    db.session.commit()
    flash("Rol actualizado.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.route("/charlas/<int:talk_id>/editar", methods=["GET", "POST"])
@roles_required("admin")
@limiter.limit("30 per minute", key_func=rate_limit_principal, methods=["POST"])
def edit_talk(talk_id: int):
    talk = db.get_or_404(Talk, talk_id)
    if talk.status != "scheduled":
        flash("Solo se pueden editar charlas programadas.", "error")
        return redirect(url_for("admin.dashboard"))
    if request.method == "GET":
        event_local = local_datetime(talk.event_at)
        return render_template("admin/edit_talk.html", talk=talk, event_local=event_local)

    try:
        payload = ProposalSchema().load(request.form.to_dict())
        event_at = parse_local_datetime(request.form["event_date"], request.form["event_time"])
    except (ValidationError, ValueError, KeyError) as error:
        messages = (
            error.messages
            if isinstance(error, ValidationError)
            else {"event_date": ["Indica una fecha y hora válidas."]}
        )
        event_local = local_datetime(talk.event_at)
        return render_template(
            "admin/edit_talk.html",
            talk=talk,
            event_local=event_local,
            errors=messages,
            form=request.form,
        ), 400

    if event_at <= utc_now():
        return render_template(
            "admin/edit_talk.html",
            talk=talk,
            event_local=local_datetime(talk.event_at),
            errors={"event_date": ["La fecha debe estar en el futuro."]},
            form=request.form,
        ), 400
    collision = Talk.query.filter(
        Talk.id != talk.id,
        Talk.status == "scheduled",
        Talk.event_at == event_at,
    ).first()
    if collision:
        return render_template(
            "admin/edit_talk.html",
            talk=talk,
            event_local=local_datetime(talk.event_at),
            errors={"event_date": ["Ya hay otra charla programada a esa hora."]},
            form=request.form,
        ), 400

    talk.title = payload["title"].strip()
    talk.description = payload["description"].strip()
    talk.event_at = event_at
    db.session.commit()
    flash("La charla se ha actualizado.", "success")
    return redirect(url_for("public.talk_detail", talk_id=talk.id))


@admin_bp.post("/charlas/<int:talk_id>/realizada")
@roles_required("admin")
@limiter.limit("20 per minute", key_func=rate_limit_principal)
def mark_completed(talk_id: int):
    talk = db.get_or_404(Talk, talk_id)
    if talk.status != "scheduled":
        abort(400)
    if talk.event_at > utc_now():
        flash("No se puede marcar como realizada antes de su fecha y hora.", "error")
        return redirect(url_for("admin.dashboard"))
    talk.status = "completed"
    talk.completed_at = utc_now()
    db.session.commit()
    flash("Charla marcada como realizada; se ha abierto el periodo de valoración.", "success")
    return redirect(url_for("admin.dashboard"))


@admin_bp.post("/charlas/<int:talk_id>/cancelar")
@roles_required("admin")
@limiter.limit("20 per minute", key_func=rate_limit_principal)
def cancel_talk(talk_id: int):
    talk = db.get_or_404(Talk, talk_id)
    if talk.status != "scheduled":
        abort(400)
    talk.status = "cancelled"
    db.session.commit()
    flash("La charla se ha cancelado.", "success")
    return redirect(url_for("admin.dashboard"))


@admin_bp.post("/comentarios/<int:comment_id>/eliminar")
@roles_required("admin")
@limiter.limit("20 per minute", key_func=rate_limit_principal)
def delete_comment(comment_id: int):
    comment = db.get_or_404(Comment, comment_id)
    talk_id = comment.talk_id
    db.session.delete(comment)
    db.session.commit()
    flash("Comentario eliminado.", "success")
    return redirect(url_for("public.talk_detail", talk_id=talk_id) + "#comentarios")
