from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.config import settings
from app.models import TimestampPrecision


@dataclass
class ParsedTimestamp:
    value: datetime
    precision: TimestampPrecision


PATTERNS: list[tuple[re.Pattern[str], str, TimestampPrecision]] = [
    (re.compile(r"(20\d{2})(\d{2})(\d{2})[_\- ]?(\d{2})(\d{2})(\d{2})"), "%Y%m%d%H%M%S", TimestampPrecision.second),
    (re.compile(r"(20\d{2})[-_. ](\d{2})[-_. ](\d{2})[-_. ](\d{2})[-_. ](\d{2})[-_. ](\d{2})"), "%Y%m%d%H%M%S", TimestampPrecision.second),
    (re.compile(r"(20\d{2})(\d{2})(\d{2})"), "%Y%m%d", TimestampPrecision.date),
]


def parse_filename_timestamp(filename: str) -> ParsedTimestamp | None:
    stem = filename.rsplit(".", 1)[0]
    for pattern, fmt, precision in PATTERNS:
        match = pattern.search(stem)
        if not match:
            continue
        raw = "".join(match.groups())
        try:
            parsed = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        localized = parsed.replace(tzinfo=ZoneInfo(settings.default_timezone))
        return ParsedTimestamp(localized.astimezone(timezone.utc), precision)

    epoch_match = re.search(r"(?<!\d)(1\d{9}|1\d{12})(?!\d)", stem)
    if epoch_match:
        raw = epoch_match.group(1)
        stamp = datetime.fromtimestamp(int(raw) / (1000 if len(raw) == 13 else 1), tz=timezone.utc)
        return ParsedTimestamp(stamp, TimestampPrecision.second)
    return None

