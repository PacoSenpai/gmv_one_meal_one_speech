from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlparse

from flask import (
    Blueprint,
    current_app,
    flash,
    g,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_jwt_extended import (
    create_access_token,
    get_jwt,
    jwt_required,
    set_access_cookies,
    unset_jwt_cookies,
)
from flask_limiter.util import get_remote_address
from marshmallow import ValidationError

from app.extensions import db, limiter
from app.models import User
from app.rate_limits import login_attempt_key, rate_limit_principal
from app.schemas import AccountSchema, PasswordSchema, RegistrationSchema
from app.security import PREAUTH_CSRF_SESSION_KEY, roles_required, validate_preauth_csrf

auth_bp = Blueprint("auth", __name__)


def _wants_long_session() -> bool:
    return "1" in request.form.getlist("remember")


def _issue_login_response(user: User, destination: str, *, remember: bool):
    expires_delta = (
        timedelta(days=current_app.config["SESSION_REMEMBER_DAYS"])
        if remember
        else current_app.config["JWT_ACCESS_TOKEN_EXPIRES"]
    )
    token = create_access_token(
        identity=str(user.id),
        additional_claims={"auth_version": user.auth_version, "remember": remember},
        expires_delta=expires_delta,
    )
    response = make_response(redirect(destination))
    set_access_cookies(response, token, max_age=int(expires_delta.total_seconds()))
    return response


def _safe_next_url(target: str | None) -> str:
    if (
        not target
        or len(target) > 2048
        or any(ord(character) < 32 or ord(character) == 127 for character in target)
    ):
        return url_for("public.index")
    if "\\" in target:
        return url_for("public.index")
    try:
        parsed = urlparse(target)
    except ValueError:
        return url_for("public.index")
    if (
        parsed.netloc
        or parsed.scheme
        or not parsed.path.startswith("/")
        or parsed.path.startswith("//")
        or parsed.path.startswith("/\\")
    ):
        return url_for("public.index")
    return target


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute", key_func=login_attempt_key, methods=["POST"])
@limiter.limit("30 per hour", key_func=get_remote_address, methods=["POST"])
def login():
    if request.method == "GET":
        return render_template("auth/login.html", next_url=request.args.get("next", "")[:2048])

    validate_preauth_csrf()

    username = request.form.get("username", "").strip().lower()
    password = request.form.get("password", "")
    if not username or len(username) > 30 or not password or len(password) > 256:
        flash("Usuario o contraseña incorrectos.", "error")
        return render_template("auth/login.html", next_url=request.form.get("next", "")[:2048]), 401

    user = User.query.filter_by(username=username, is_active=True).first()
    if user is None or not user.check_password(password):
        flash("Usuario o contraseña incorrectos.", "error")
        return render_template("auth/login.html", next_url=request.form.get("next", "")[:2048]), 401

    destination = (
        url_for("auth.change_password")
        if user.must_change_password
        else _safe_next_url(request.form.get("next"))
    )
    session.pop(PREAUTH_CSRF_SESSION_KEY, None)
    return _issue_login_response(user, destination, remember=_wants_long_session())


@auth_bp.route("/registro", methods=["GET", "POST"])
@limiter.limit("5 per hour", key_func=get_remote_address, methods=["POST"])
@limiter.limit("12 per day", key_func=get_remote_address, methods=["POST"])
def register():
    if request.method == "GET":
        return render_template("auth/register.html")

    validate_preauth_csrf()

    try:
        payload = RegistrationSchema().load(request.form.to_dict())
    except ValidationError as error:
        return render_template("auth/register.html", errors=error.messages, form=request.form), 400

    username = payload["username"].strip().lower()
    if User.query.filter_by(username=username).first():
        return render_template(
            "auth/register.html",
            errors={"username": ["Ese nombre de usuario ya está en uso."]},
            form=request.form,
        ), 400

    user = User(username=username, full_name=payload["full_name"].strip() or None, role="member")
    user.set_password(payload["password"])
    db.session.add(user)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    session.pop(PREAUTH_CSRF_SESSION_KEY, None)
    flash("Cuenta creada. ¡Bienvenido a las charlas!", "success")
    return _issue_login_response(
        user, url_for("public.index"), remember=_wants_long_session()
    )


@auth_bp.route("/logout", methods=["POST"])
@jwt_required()
@limiter.limit("30 per minute", key_func=rate_limit_principal)
def logout():
    response = make_response(redirect(url_for("public.index")))
    unset_jwt_cookies(response)
    flash("Has cerrado la sesión.", "success")
    return response


@auth_bp.route("/cuenta", methods=["GET", "POST"])
@roles_required("member", "admin")
@limiter.limit("60 per minute", key_func=rate_limit_principal, methods=["POST"])
def account():
    user: User = g.current_user
    if request.method == "GET":
        return render_template("auth/account.html")

    try:
        payload = AccountSchema().load(request.form.to_dict())
    except ValidationError as error:
        return render_template("auth/account.html", errors=error.messages, form=request.form), 400

    username = payload["username"].strip().lower()
    duplicate = User.query.filter(User.username == username, User.id != user.id).first()
    if duplicate:
        return render_template(
            "auth/account.html",
            errors={"username": ["Ese nombre de usuario ya está en uso."]},
            form=request.form,
        ), 400

    user.username = username
    user.full_name = payload["full_name"].strip() or None
    db.session.commit()
    flash("Tu perfil se ha actualizado.", "success")
    return redirect(url_for("auth.account"))


@auth_bp.route("/cuenta/contrasena", methods=["GET", "POST"])
@roles_required("member", "admin")
@limiter.limit("5 per hour", key_func=rate_limit_principal, methods=["POST"])
def change_password():
    user: User = g.current_user
    forced = user.must_change_password
    if request.method == "GET":
        return render_template("auth/change_password.html", forced=forced)

    try:
        payload = PasswordSchema().load(request.form.to_dict())
    except ValidationError as error:
        return render_template("auth/change_password.html", forced=forced, errors=error.messages), 400

    if not forced and not user.check_password(payload["current_password"]):
        return render_template(
            "auth/change_password.html",
            forced=forced,
            errors={"current_password": ["La contraseña actual no es correcta."]},
        ), 400

    if user.check_password(payload["new_password"]):
        return render_template(
            "auth/change_password.html",
            forced=forced,
            errors={"new_password": ["Elige una contraseña distinta de la actual."]},
        ), 400

    user.set_password(payload["new_password"])
    user.must_change_password = False
    user.auth_version += 1
    db.session.commit()
    flash("Contraseña actualizada correctamente.", "success")
    return _issue_login_response(
        user,
        url_for("public.index"),
        remember=bool(get_jwt().get("remember", False)),
    )
