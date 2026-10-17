from __future__ import annotations

import hashlib
import secrets
from datetime import date, datetime, time

from sqlalchemy import update

from app.extensions import db
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


def consume_invitation_if_valid(code: str) -> bool:
    """Atomically mark one valid code used within the account-creation transaction."""
    current_time = utc_now()
    result = db.session.execute(
        update(Invitation)
        .where(
            Invitation.code_hash == hash_invitation_code(code),
            Invitation.used_at.is_(None),
            Invitation.expires_at >= current_time,
        )
        .values(used_at=current_time)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1
