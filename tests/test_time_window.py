from datetime import UTC, datetime

import pytest

from electricity_alert.models import TimeWindow


def window_at(utc: str, days: int = 1) -> TimeWindow:
    return TimeWindow.upcoming(datetime.fromisoformat(utc).replace(tzinfo=UTC), days)


@pytest.mark.parametrize(
    ("utc", "start", "last_day"),
    [
        ("2026-10-13T10:00:00", "2026-10-13 12:00:00", "2026-10-14"),  # ordinary day (CEST)
        ("2026-10-13T22:30:00", "2026-10-14 00:30:00", "2026-10-15"),  # Warsaw date, not UTC date
        ("2026-03-28T19:00:00", "2026-03-28 20:00:00", "2026-03-29"),  # day before summer time
        ("2026-03-28T23:30:00", "2026-03-29 00:30:00", "2026-03-30"),  # just after midnight, switch day
        ("2026-10-24T18:00:00", "2026-10-24 20:00:00", "2026-10-25"),  # day before winter time
        ("2026-10-25T00:30:00", "2026-10-25 02:30:00", "2026-10-26"),  # repeated hour (CEST)
        ("2026-10-25T01:30:00", "2026-10-25 02:30:00", "2026-10-26"),  # repeated hour (CET)
        ("2026-12-31T12:00:00", "2026-12-31 13:00:00", "2027-01-01"),  # new year's eve
        ("2026-12-31T22:59:59", "2026-12-31 23:59:59", "2027-01-01"),
        ("2026-12-31T23:00:00", "2027-01-01 00:00:00", "2027-01-02"),
    ],
)
def test_from_now_to_end_of_tomorrow_in_warsaw(utc, start, last_day):
    window = window_at(utc)
    assert window.start_text == start
    assert window.end_text == f"{last_day} 23:59:59"


def test_upcoming_days_at_least_tomorrow():
    assert window_at("2026-12-30T12:00:00", days=4).end_text == "2027-01-03 23:59:59"
    assert window_at("2026-12-30T12:00:00", days=0) == window_at("2026-12-30T12:00:00", days=1)


def test_fractions_of_a_second_dropped():
    assert window_at("2026-10-13T10:00:01.987").start == datetime(2026, 10, 13, 12, 0, 1)
