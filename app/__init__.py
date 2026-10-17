from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from flask import Flask, g, redirect, request, url_for
from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request
from flask_jwt_extended.exceptions import JWTExtendedException
from flask_restful import Api
from sqlalchemy import event

from app.blueprints.admin import admin_bp
from app.blueprints.auth import auth_bp
from app.blueprints.public import public_bp
from app.blueprints.talks import talks_bp
from app.cli import register_cli_commands
from app.extensions import db, jwt, migrate
from app.models import User, utc_now
from app.resources import HealthResource, PublicStatisticsResource, PublicTalksResource


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object("app.config.Config")
    if test_config:
        app.config.update(test_config)

    os.makedirs(app.instance_path, exist_ok=True)
    upload_folder = Path(app.config.get("UPLOAD_FOLDER") or Path(app.instance_path) / "uploads")
    upload_folder.mkdir(parents=True, exist_ok=True)
    app.config["UPLOAD_FOLDER"] = str(upload_folder)

    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)
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
            return redirect(url_for("auth.change_password", forced=1))

    @app.context_processor
    def inject_template_helpers() -> dict[str, Any]:
        from app.services.dates import local_datetime

        return {
            "current_user": getattr(g, "current_user", None),
            "csrf_token": request.cookies.get(app.config["JWT_ACCESS_CSRF_COOKIE_NAME"], ""),
            "local_datetime": local_datetime,
            "now": utc_now(),
        }

    @app.errorhandler(413)
    def request_too_large(_error: Exception):
        return "El archivo supera el tamaño permitido.", 413

    return app
