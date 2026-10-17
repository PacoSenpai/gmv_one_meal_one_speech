from __future__ import annotations

import calendar
from datetime import UTC, datetime, time

from flask import has_app_context

from app.models import ScheduleRule, ScheduleSetting, Talk, utc_now
from app.services.dates import app_timezone, to_utc_naive

ScheduleRuleValue = tuple[int, int]
DEFAULT_SCHEDULE_TIME = time(13, 30)
DEFAULT_SCHEDULE_RULES: tuple[ScheduleRuleValue, ...] = ((calendar.TUESDAY, 1),)


def get_schedule_configuration() -> tuple[time, tuple[ScheduleRuleValue, ...]]:
    """Return the saved monthly rules, or the original first-Tuesday default."""
    if not has_app_context():
        return DEFAULT_SCHEDULE_TIME, DEFAULT_SCHEDULE_RULES

    setting = ScheduleSetting.query.filter_by(id=1).first()
    if setting is None:
        return DEFAULT_SCHEDULE_TIME, DEFAULT_SCHEDULE_RULES

    rules = tuple(
        (rule.weekday, rule.occurrence)
        for rule in ScheduleRule.query.order_by(ScheduleRule.weekday, ScheduleRule.occurrence).all()
    )
    return setting.default_time, rules or DEFAULT_SCHEDULE_RULES


def next_default_slot(
    *, now: datetime | None = None, occupied: list[datetime] | None = None
) -> datetime:
    """Return the next unoccupied configured monthly slot, stored as naive UTC."""
    default_time, rules = get_schedule_configuration()
    return next_schedule_slots(
        count=1,
        now=now,
        occupied=occupied,
        default_time=default_time,
        rules=rules,
    )[0]


def next_schedule_slots(
    *,
    count: int,
    now: datetime | None = None,
    occupied: list[datetime] | None = None,
    default_time: time | None = None,
    rules: tuple[ScheduleRuleValue, ...] | list[ScheduleRuleValue] | None = None,
) -> list[datetime]:
    """Generate the next configured monthly slots after ``now`` in Madrid time."""
    if count <= 0:
        return []

    if default_time is None or rules is None:
        saved_time, saved_rules = get_schedule_configuration()
    else:
        saved_time, saved_rules = default_time, tuple(rules)
    slot_time = saved_time if default_time is None else default_time
    slot_rules = tuple(sorted(set(rules if rules is not None else saved_rules)))
    if not slot_rules or any(
        not 0 <= weekday <= 6 or not 1 <= occurrence <= 5 for weekday, occurrence in slot_rules
    ):
        raise ValueError("Configura al menos un día de exposición válido.")

    current_utc = _as_utc_naive(now or utc_now())
    local_now = current_utc.replace(tzinfo=UTC).astimezone(app_timezone())
    occupied_slots = set(occupied or [])
    year, month = local_now.year, local_now.month
    slots: list[datetime] = []

    for _ in range(1200):
        monthly_slots: list[datetime] = []
        for weekday, occurrence in slot_rules:
            day = _occurrence_day(year, month, weekday, occurrence)
            if day is None:
                continue
            local_slot = datetime(year, month, day, slot_time.hour, slot_time.minute, tzinfo=app_timezone())
            monthly_slots.append(to_utc_naive(local_slot))

        for candidate in sorted(monthly_slots):
            if candidate <= current_utc or candidate in occupied_slots:
                continue
            slots.append(candidate)
            if len(slots) == count:
                return slots

        month += 1
        if month > 12:
            year += 1
            month = 1

    raise RuntimeError("No se encontraron suficientes huecos en los próximos cien años.")


def occupied_default_slots() -> list[datetime]:
    return [
        talk.event_at
        for talk in Talk.query.filter_by(status="scheduled").all()
    ]


def reschedule_future_talks(
    *,
    default_time: time,
    rules: tuple[ScheduleRuleValue, ...] | list[ScheduleRuleValue],
    now: datetime | None = None,
) -> list[tuple[int, datetime, datetime]]:
    """Reassign every future scheduled talk in its existing chronological order."""
    current_utc = _as_utc_naive(now or utc_now())
    talks = (
        Talk.query.filter(Talk.status == "scheduled", Talk.event_at > current_utc)
        .order_by(Talk.event_at.asc(), Talk.id.asc())
        .all()
    )
    if not talks:
        return []

    slots = next_schedule_slots(
        count=len(talks),
        now=current_utc,
        default_time=default_time,
        rules=rules,
    )
    changes: list[tuple[int, datetime, datetime]] = []
    for talk, new_event_at in zip(talks, slots, strict=True):
        old_event_at = talk.event_at
        if old_event_at != new_event_at:
            changes.append((talk.id, old_event_at, new_event_at))
        talk.event_at = new_event_at
    return changes


def _as_utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).replace(tzinfo=None)


def _occurrence_day(year: int, month: int, weekday: int, occurrence: int) -> int | None:
    first_weekday, days_in_month = calendar.monthrange(year, month)
    first_occurrence = 1 + (weekday - first_weekday) % 7
    day = first_occurrence + (occurrence - 1) * 7
    return day if day <= days_in_month else None


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
