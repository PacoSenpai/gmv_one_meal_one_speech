from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.extensions import db
from app.models import Talk, User
from app.services.scheduling import is_evaluation_open, next_default_slot


def test_default_slot_uses_first_tuesday_at_lunch():
    now = datetime(2026, 10, 17, 9, 0, tzinfo=UTC)
    slot = next_default_slot(now=now)
    local_slot = slot.replace(tzinfo=UTC).astimezone(ZoneInfo("Europe/Madrid"))
    assert local_slot.isoformat().startswith("2026-11-03T13:30:00")


def test_default_slot_skips_occupied_month():
    now = datetime(2026, 10, 17, 9, 0, tzinfo=UTC)
    november = next_default_slot(now=now)
    slot = next_default_slot(now=now, occupied=[november])
    local_slot = slot.replace(tzinfo=UTC).astimezone(ZoneInfo("Europe/Madrid"))
    assert local_slot.isoformat().startswith("2026-12-01T13:30:00")


def test_evaluation_is_open_until_next_scheduled_talk(app):
    with app.app_context():
        speaker = User(username="speaker", password_hash="not-a-login-password")
        db.session.add(speaker)
        db.session.flush()
        now = datetime(2026, 11, 4, 12, 0)
        completed = Talk(
            title="Completed talk",
            description="A sufficiently long description for a completed talk.",
            event_at=now - timedelta(days=2),
            completed_at=now - timedelta(days=1),
            status="completed",
            speaker_id=speaker.id,
        )
        following = Talk(
            title="Next talk",
            description="A sufficiently long description for a future talk.",
            event_at=now + timedelta(days=1),
            status="scheduled",
            speaker_id=speaker.id,
        )
        db.session.add_all([completed, following])
        db.session.flush()
        assert is_evaluation_open(completed, now=now)
        assert not is_evaluation_open(completed, now=following.event_at)
