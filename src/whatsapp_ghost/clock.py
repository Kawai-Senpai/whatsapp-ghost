from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone


def parse_duration(value: str) -> timedelta:
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    compact = value.strip().lower().replace(" ", "")
    matches = list(re.finditer(r"(\d+(?:\.\d+)?)([smhd])", compact))
    if not matches or "".join(match.group(0) for match in matches) != compact:
        raise ValueError("Duration must look like 30s, 5m, 23h, 2d, or 1d2h30m")
    return timedelta(seconds=sum(float(match.group(1)) * units[match.group(2)] for match in matches))


def parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
