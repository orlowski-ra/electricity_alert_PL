"""Quiet hours: notifications between 22:00 and 07:00 local time are delivered silently."""

from __future__ import annotations

from datetime import datetime, time, tzinfo

START = time(22, 0)
END = time(7, 0)


def is_quiet(now: datetime, zone: tzinfo) -> bool:
    local = now.astimezone(zone).time()
    return local >= START or local < END
