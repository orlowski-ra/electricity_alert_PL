"""Core data types shared by every provider.

Dates of outages are naive ``datetime`` objects in Polish local time (Europe/Warsaw), because
that is what the operators publish. Moments in time ("now", "last notified") are timezone-aware.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class ProviderId(StrEnum):
    PGE = "pge"
    TAURON = "tauron"
    ENEA = "enea"
    ENERGA = "energa"
    STOEN = "stoen"


@dataclass(frozen=True)
class Address:
    """A saved address. ``params`` holds provider-specific keys (see each provider module)."""

    id: int
    provider: ProviderId
    label: str
    params: dict[str, str]
    paused_until: datetime | None = None


@dataclass(frozen=True)
class Outage:
    uuid: str
    numbers: tuple[str, ...] = ()
    start_at: datetime | None = None
    stop_at: datetime | None = None
    revoked: bool = False
    description: str | None = None


# --- ApiResult: errors are values, never exceptions -------------------------------------------


@dataclass(frozen=True)
class Success[T]:
    data: T


@dataclass(frozen=True)
class NetworkError:
    """No network, timeout, connection reset."""


@dataclass(frozen=True)
class HttpError:
    code: int


@dataclass(frozen=True)
class FormatError:
    """The response does not look like what we expect (the unofficial API changed)."""


@dataclass(frozen=True)
class NotSupported:
    pass


ApiError = NetworkError | HttpError | FormatError | NotSupported
type ApiResult[T] = Success[T] | ApiError


def is_success(result: object) -> bool:
    return isinstance(result, Success)


# --- Time window -------------------------------------------------------------------------------


@dataclass(frozen=True)
class TimeWindow:
    """Interval in Polish local time (naive datetimes), as the operators' APIs expect."""

    start: datetime
    end: datetime

    @property
    def start_text(self) -> str:
        return self.start.strftime(DATE_FORMAT)

    @property
    def end_text(self) -> str:
        return self.end.strftime(DATE_FORMAT)

    def overlaps(self, start: datetime | None, stop: datetime | None) -> bool:
        """Missing dates count as overlapping: better a false alert than a missed one."""
        return (stop is None or stop >= self.start) and (start is None or start <= self.end)

    @staticmethod
    def upcoming(now: datetime, days: int = 1) -> TimeWindow:
        """From now until the end of day "today + days" in Poland (``days`` at least 1).

        Starts at *now*, not tomorrow: an outage announced in the evening for tomorrow but only
        checked after midnight, or announced in the morning for today, still gets a notification.
        """
        local = now.astimezone(WARSAW).replace(tzinfo=None, microsecond=0)
        last_day = local.date() + timedelta(days=max(days, 1))
        return TimeWindow(start=local, end=datetime.combine(last_day, time(23, 59, 59)))


@dataclass(frozen=True)
class NotifiedOutage:
    """A row of the "already notified" table."""

    uuid: str
    revoked_notified: bool
    notified_at: datetime | None = None


@dataclass(frozen=True)
class NotificationDecision:
    new_outages: list[Outage] = field(default_factory=list)
    revoked_outages: list[Outage] = field(default_factory=list)
    paused_until: datetime | None = None
