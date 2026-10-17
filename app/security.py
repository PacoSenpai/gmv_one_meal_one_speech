from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import TypeVar, cast

from flask import abort, g
from flask_jwt_extended import verify_jwt_in_request

from app.models import User

ViewFunction = TypeVar("ViewFunction", bound=Callable[..., object])


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
