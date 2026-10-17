from __future__ import annotations

import calendar
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from flask import current_app, has_app_context


def app_timezone() -> ZoneInfo:
    if not has_app_context():
        return ZoneInfo("Europe/Madrid")
    return ZoneInfo(current_app.config["TIMEZONE"])


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def local_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC).astimezone(app_timezone())


def to_utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=app_timezone())
    return value.astimezone(UTC).replace(tzinfo=None)


def parse_local_datetime(date_value: str, time_value: str) -> datetime:
    local_value = datetime.combine(date.fromisoformat(date_value), time.fromisoformat(time_value))
    return to_utc_naive(local_value.replace(tzinfo=app_timezone()))


def first_tuesday(year: int, month: int) -> date:
    month_calendar = calendar.monthcalendar(year, month)
    day = next(week[calendar.TUESDAY] for week in month_calendar if week[calendar.TUESDAY])
    return date(year, month, day)
