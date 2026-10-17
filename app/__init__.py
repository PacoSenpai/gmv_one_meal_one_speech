from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from flask import Flask, g, redirect, request, url_for
from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request
from flask_jwt_extended.exceptions import JWTExtendedException
from flask_restful import Api
from sqlalchemy import event
from werkzeug.middleware.proxy_fix import ProxyFix

from app.blueprints.admin import admin_bp
from app.blueprints.auth import auth_bp
from app.blueprints.public import public_bp
from app.blueprints.talks import talks_bp
from app.cli import register_cli_commands
from app.extensions import db, jwt, limiter, migrate
from app.models import User, utc_now
from app.resources import HealthResource, PublicStatisticsResource, PublicTalksResource
from app.security import preauth_csrf_token


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object("app.config.Config")
    if test_config:
        app.config.update(test_config)
    _validate_security_config(app)

    trusted_proxy_count = app.config["TRUSTED_PROXY_COUNT"]
    if not 0 <= trusted_proxy_count <= 5:
        raise RuntimeError("TRUSTED_PROXY_COUNT debe estar entre 0 y 5.")
    if trusted_proxy_count:
        # Only trust X-Forwarded-For when the operator explicitly configures
        # the exact proxy count and those proxies replace untrusted headers.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=trusted_proxy_count)

    os.makedirs(app.instance_path, exist_ok=True)
    upload_folder = Path(app.config.get("UPLOAD_FOLDER") or Path(app.instance_path) / "uploads")
    upload_folder.mkdir(parents=True, exist_ok=True)
    app.config["UPLOAD_FOLDER"] = str(upload_folder)

    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)
    limiter.init_app(app)
    with app.app_context():
        if db.engine.dialect.name == "sqlite":
            @event.listens_for(db.engine, "connect")
            def enable_sqlite_foreign_keys(connection: Any, _record: Any) -> None:
                cursor = connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

    app_api = Api()
    app_api.add_resource(HealthResource, "/api/health")
    app_api.add_resource(PublicTalksResource, "/api/talks")
    app_api.add_resource(PublicStatisticsResource, "/api/statistics")
    app_api.init_app(app)

    app.register_blueprint(auth_bp)
    app.register_blueprint(public_bp)
    app.register_blueprint(talks_bp)
    app.register_blueprint(admin_bp)
    register_cli_commands(app)

    @jwt.token_in_blocklist_loader
    def is_token_revoked(jwt_header: dict[str, Any], jwt_payload: dict[str, Any]) -> bool:
        user_id = jwt_payload.get("sub")
        if not user_id:
            return True
        try:
            user = db.session.get(User, int(user_id))
        except (TypeError, ValueError):
            return True
        if user is None or not user.is_active:
            return True
        return jwt_payload.get("auth_version") != user.auth_version

    @jwt.unauthorized_loader
    def redirect_unauthorized(_reason: str):
        if request.path.startswith("/api/"):
            return {"error": "authentication_required"}, 401
        return redirect(url_for("auth.login", next=request.full_path))

    @jwt.invalid_token_loader
    def invalid_token(_reason: str):
        if request.path.startswith("/api/"):
            return {"error": "invalid_token"}, 401
        return redirect(url_for("auth.login"))

    @app.before_request
    def load_current_user() -> None:
        g.current_user = None
        # Login and registration POSTs use an independent session-bound CSRF token.
        # GETs still load the account so forced password changes remain enforced.
        if request.endpoint in {"auth.login", "auth.register"} and request.method == "POST":
            return
        try:
            verify_jwt_in_request(optional=True)
        except JWTExtendedException:
            return

        user_id = get_jwt_identity()
        if not user_id:
            return

        try:
            g.current_user = db.session.get(User, int(user_id))
        except (TypeError, ValueError):
            g.current_user = None

        if g.current_user and not g.current_user.is_active:
            g.current_user = None
            return

        endpoint = request.endpoint or ""
        allowed_during_password_change = {
            "auth.change_password",
            "auth.logout",
            "static",
        }
        if (
            g.current_user
            and g.current_user.must_change_password
            and endpoint not in allowed_during_password_change
        ):
            return redirect(url_for("auth.change_password"))

    @app.context_processor
    def inject_template_helpers() -> dict[str, Any]:
        from app.services.dates import local_datetime

        preauth_form = request.endpoint in {"auth.login", "auth.register"}
        if preauth_form:
            csrf_form_token = preauth_csrf_token()
        elif getattr(g, "current_user", None):
            csrf_form_token = request.cookies.get(app.config["JWT_ACCESS_CSRF_COOKIE_NAME"], "")
        else:
            csrf_form_token = ""
        return {
            "current_user": None if preauth_form else getattr(g, "current_user", None),
            "csrf_token": csrf_form_token,
            "local_datetime": local_datetime,
            "now": utc_now(),
        }

    @app.errorhandler(413)
    def request_too_large(_error: Exception):
        return "El archivo supera el tamaño permitido.", 413

    @app.errorhandler(429)
    def too_many_requests(_error: Exception):
        response = _error.get_response()
        if request.path.startswith("/api/"):
            response.set_data(app.json.dumps({"error": "rate_limit_exceeded"}))
            response.content_type = "application/json"
        else:
            response.set_data("Demasiadas solicitudes. Espera un momento e inténtalo de nuevo.")
            response.content_type = "text/plain; charset=utf-8"
        return response

    return app


def _validate_security_config(app: Flask) -> None:
    is_production = (
        str(app.config.get("APP_ENV", "development")).strip().lower() == "production"
        or bool(app.config.get("DATABASE_PATH"))
    )
    if app.testing and not is_production:
        return

    for setting_name in ("SECRET_KEY", "JWT_SECRET_KEY"):
        value = app.config.get(setting_name)
        if not value or len(value.encode("utf-8")) < 32:
            raise RuntimeError(f"Configura {setting_name} con al menos 32 bytes aleatorios.")

    if app.config["SECRET_KEY"] == app.config["JWT_SECRET_KEY"]:
        raise RuntimeError("SECRET_KEY y JWT_SECRET_KEY deben ser valores distintos.")

    if not is_production:
        return

    if not app.config["JWT_COOKIE_SECURE"]:
        raise RuntimeError("En producción configura COOKIE_SECURE=1 para proteger las cookies JWT.")

    storage_uri = app.config["RATELIMIT_STORAGE_URI"]
    if not storage_uri.startswith(("redis://", "rediss://", "redis+unix://")):
        raise RuntimeError("En producción RATELIMIT_STORAGE_URI debe apuntar a un backend Redis compartido.")
