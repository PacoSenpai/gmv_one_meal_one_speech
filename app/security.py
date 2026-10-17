from __future__ import annotations

import hmac
import secrets
from collections.abc import Callable
from functools import wraps
from typing import TypeVar, cast

from flask import abort, g, request, session
from flask_jwt_extended import verify_jwt_in_request

from app.models import User

ViewFunction = TypeVar("ViewFunction", bound=Callable[..., object])
PREAUTH_CSRF_SESSION_KEY = "_preauth_csrf_token"


def preauth_csrf_token() -> str:
    token = session.get(PREAUTH_CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[PREAUTH_CSRF_SESSION_KEY] = token
    return token


def validate_preauth_csrf() -> None:
    """Validate the session token used by public login and registration forms."""
    expected_token = session.get(PREAUTH_CSRF_SESSION_KEY, "")
    submitted_token = request.form.get("csrf_token", "")
    if not expected_token or not submitted_token or not hmac.compare_digest(
        str(expected_token), submitted_token
    ):
        abort(400)


def roles_required(*allowed_roles: str) -> Callable[[ViewFunction], ViewFunction]:
    def decorator(view: ViewFunction) -> ViewFunction:
        @wraps(view)
        def wrapped(*args: object, **kwargs: object) -> object:
            verify_jwt_in_request()
            user: User | None = getattr(g, "current_user", None)
            if user is None or user.role not in allowed_roles:
                abort(403)
            return view(*args, **kwargs)

        return cast(ViewFunction, wrapped)

    return decorator
