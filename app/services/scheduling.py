from __future__ import annotations

from datetime import UTC, datetime, time

from app.models import Talk
from app.services.dates import app_timezone, first_tuesday, to_utc_naive


def next_default_slot(
    *, now: datetime | None = None, occupied: list[datetime] | None = None
) -> datetime:
    """Return the next unoccupied first Tuesday at 13:30 Madrid time, stored as naive UTC."""
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    local_now = current.astimezone(app_timezone())
    occupied_slots = set(occupied or [])
    year, month = local_now.year, local_now.month

    for _ in range(120):
        local_candidate = datetime.combine(first_tuesday(year, month), time(13, 30), app_timezone())
        candidate = to_utc_naive(local_candidate)
        if candidate > local_now.astimezone(UTC).replace(tzinfo=None) and candidate not in occupied_slots:
            return candidate

        month += 1
        if month > 12:
            year += 1
            month = 1

    raise RuntimeError("No se encontró un hueco disponible en los próximos diez años.")


def occupied_default_slots() -> list[datetime]:
    return [
        talk.event_at
        for talk in Talk.query.filter_by(status="scheduled").all()
    ]


def next_scheduled_talk(*, after: datetime | None = None) -> Talk | None:
    current_time = after or datetime.now(UTC).replace(tzinfo=None)
    return (
        Talk.query.filter(Talk.status == "scheduled", Talk.event_at > current_time)
        .order_by(Talk.event_at.asc())
        .first()
    )


def evaluation_closes_at(talk: Talk) -> datetime | None:
    if talk.completed_at is None:
        return None
    next_talk = (
        Talk.query.filter(
            Talk.status == "scheduled",
            Talk.event_at > talk.completed_at,
        )
        .order_by(Talk.event_at.asc())
        .first()
    )
    return next_talk.event_at if next_talk else None


def is_evaluation_open(talk: Talk, *, now: datetime | None = None) -> bool:
    if talk.status != "completed" or talk.completed_at is None:
        return False
    current_time = now or datetime.now(UTC).replace(tzinfo=None)
    closes_at = evaluation_closes_at(talk)
    return current_time >= talk.completed_at and (closes_at is None or current_time < closes_at)
