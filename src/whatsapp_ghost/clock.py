from __future__ import annotations

from datetime import datetime, timedelta, timezone


def parse_duration(value: str) -> timedelta:
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    try:
        return timedelta(seconds=float(value[:-1]) * units[value[-1].lower()])
    except (KeyError, ValueError, IndexError) as exc:
        raise ValueError("Duration must look like 30s, 5m, 23h, or 2d") from exc


def parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

