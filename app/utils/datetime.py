from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import settings


@lru_cache(maxsize=1)
def app_timezone():
    timezone_name = (settings.APP_TIMEZONE or "").strip()
    if timezone_name:
        try:
            return ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            pass
    return timezone(timedelta(hours=5))


def local_today() -> date:
    return datetime.now(app_timezone()).date()


def local_now() -> datetime:
    return datetime.now(app_timezone())


def to_local_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=app_timezone())
    return value.astimezone(app_timezone())


def format_local_datetime(value: datetime | None, fmt: str = "%d.%m.%Y %H:%M") -> str:
    local_value = to_local_datetime(value)
    return local_value.strftime(fmt) if local_value else ""


def excel_local_datetime(value: datetime | None) -> datetime | None:
    local_value = to_local_datetime(value)
    return local_value.replace(tzinfo=None) if local_value else None