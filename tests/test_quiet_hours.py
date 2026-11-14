from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from electricity_alert.quiet_hours import is_quiet

WARSAW = ZoneInfo("Europe/Warsaw")


@pytest.mark.parametrize(
    ("local", "quiet"),
    [("21:59", False), ("22:00", True), ("23:30", True), ("03:00", True), ("06:59", True), ("07:00", False), ("12:00", False)],
)
def test_quiet_from_22_to_7_local_time(local, quiet):
    now = datetime.fromisoformat(f"2026-10-13T{local}:00").replace(tzinfo=timezone(timedelta(hours=2)))
    assert is_quiet(now, WARSAW) is quiet


def test_own_time_zone_counts():
    now = datetime(2026, 10, 13, 20, 30, tzinfo=UTC)  # 22:30 Warsaw, 16:30 New York
    assert is_quiet(now, WARSAW)
    assert not is_quiet(now, ZoneInfo("America/New_York"))
