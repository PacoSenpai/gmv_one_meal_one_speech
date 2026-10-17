from __future__ import annotations

import hashlib
import secrets
from datetime import date, datetime, time

from app.models import Invitation, utc_now
from app.services.dates import app_timezone, to_utc_naive


def hash_invitation_code(code: str) -> str:
    return hashlib.sha256(code.strip().encode("utf-8")).hexdigest()


def create_invitation_code(expires_on: date, created_by_id: int) -> tuple[Invitation, str]:
    code = secrets.token_urlsafe(24)
    expires_at = datetime.combine(expires_on, time.max, app_timezone())
    invitation = Invitation(
        code_hash=hash_invitation_code(code),
        expires_at=to_utc_naive(expires_at),
        created_by_id=created_by_id,
    )
    return invitation, code


def find_valid_invitation(code: str) -> Invitation | None:
    invitation = Invitation.query.filter_by(code_hash=hash_invitation_code(code)).first()
    if invitation is None or invitation.used_at is not None or invitation.expires_at < utc_now():
        return None
    return invitation
