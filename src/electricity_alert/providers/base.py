"""Provider interface."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from ..models import Address, ApiResult, Outage, ProviderId, TimeWindow

Log = Callable[[str], None]


def no_log(_: str) -> None:
    pass


class OutageProvider(Protocol):
    id: ProviderId
    display_name: str

    def planned_outages(self, address: Address, window: TimeWindow) -> ApiResult[list[Outage]]:
        """Planned outages for a saved address that overlap ``window``."""
        ...
