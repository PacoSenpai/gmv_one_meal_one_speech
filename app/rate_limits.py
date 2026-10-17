from __future__ import annotations

import hashlib
import hmac

from flask import current_app, g, request
from flask_limiter.util import get_remote_address


def rate_limit_principal() -> str:
    """Use account+source buckets for writes and source address for guests."""
    remote_address = get_remote_address()
    user = getattr(g, "current_user", None)
    if user is not None:
        return f"user:{user.id}:ip:{remote_address}"
    return f"ip:{remote_address}"


def login_attempt_key() -> str:
    """Key login attempts by source and a keyed digest, not a clear-text username."""
    username = request.form.get("username", "").strip().casefold()[:30]
    secret = str(current_app.config["SECRET_KEY"]).encode("utf-8")
    username_digest = hmac.new(secret, username.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"login:{get_remote_address()}:{username_digest}"
