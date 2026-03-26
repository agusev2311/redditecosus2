from __future__ import annotations

from datetime import datetime, timezone


def ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def seconds_between(later: datetime | None, earlier: datetime | None) -> float | None:
    left = ensure_utc(later)
    right = ensure_utc(earlier)
    if left is None or right is None:
        return None
    return (left - right).total_seconds()
