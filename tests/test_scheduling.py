from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.extensions import db
from app.models import ScheduleRule, ScheduleSetting, Talk, User
from app.services.scheduling import is_evaluation_open, next_default_slot, next_schedule_slots


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


def test_schedule_configuration_supports_multiple_week_occurrences_and_time(app):
    with app.app_context():
        db.session.add(ScheduleSetting(id=1, default_time=time(14, 0)))
        db.session.add_all(
            [
                ScheduleRule(weekday=1, occurrence=1),
                ScheduleRule(weekday=1, occurrence=2),
            ]
        )
        db.session.commit()

        slots = next_schedule_slots(now=datetime(2026, 10, 1, 6, tzinfo=UTC), count=2)

    local_slots = [slot.replace(tzinfo=UTC).astimezone(ZoneInfo("Europe/Madrid")) for slot in slots]
    assert [slot.isoformat() for slot in local_slots] == [
        "2026-10-06T14:00:00+02:00",
        "2026-10-13T14:00:00+02:00",
    ]


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
