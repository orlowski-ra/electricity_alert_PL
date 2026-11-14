"""One check of all saved addresses: fetch, decide, notify, remember."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from . import decider
from .models import Address, ApiResult, HttpError, NetworkError, Outage, Success, TimeWindow
from .providers import ProviderRegistry
from .storage import Storage

MAX_DAYS = 4


class Notifier(Protocol):
    def show_new(self, address: Address, outage: Outage, silent: bool) -> bool:
        """Shows a notification; False = could not be shown (nothing is recorded then)."""
        ...

    def show_revoked(self, address: Address, outage: Outage, silent: bool) -> bool: ...


@dataclass
class CheckReport:
    results: dict[int, ApiResult[list[Outage]]] = field(default_factory=dict)
    notified: list[tuple[Address, Outage]] = field(default_factory=list)
    revoked: list[tuple[Address, Outage]] = field(default_factory=list)

    @property
    def connection_problem(self) -> bool:
        return any(isinstance(r, (NetworkError, HttpError)) for r in self.results.values())


def _utc_now() -> datetime:
    return datetime.now(UTC)


class OutageChecker:
    def __init__(
        self,
        storage: Storage,
        registry: ProviderRegistry,
        notifier: Notifier,
        clock: Callable[[], datetime] = _utc_now,
        is_quiet: Callable[[datetime], bool] = lambda _: False,
    ) -> None:
        self._storage = storage
        self._registry = registry
        self._notifier = notifier
        self._clock = clock
        self._is_quiet = is_quiet

    def run(self, days: int = 1, notify_revoked: bool = True) -> CheckReport:
        now = self._clock()
        self._storage.cleanup_old_notified(now)
        days = min(max(days, 1), MAX_DAYS)
        window = TimeWindow.upcoming(now, days)
        silent = self._is_quiet(now)
        report = CheckReport()
        for address in self._storage.addresses():
            provider = self._registry.get(address.provider)
            if provider is None:
                continue
            result = provider.planned_outages(address, window)
            report.results[address.id] = result
            if isinstance(result, Success):
                self._handle(address, result.data, notify_revoked, now, silent, report)
        return report

    def _handle(
        self, address: Address, outages: list[Outage], notify_revoked: bool, now: datetime, silent: bool, report: CheckReport
    ) -> None:
        paused = address.paused_until is not None and address.paused_until > now
        decision = decider.decide(
            outages=outages,
            notified=self._storage.notified_for(address.id),
            notify_revoked=notify_revoked,
            is_paused=paused,
            now=now,
        )
        # Record "notified" only when the notification was really shown.
        any_new = False
        for outage in decision.new_outages:
            if self._notifier.show_new(address, outage, silent):
                self._storage.mark_notified(address.id, outage, now)
                report.notified.append((address, outage))
                any_new = True
        for outage in decision.revoked_outages:
            if self._notifier.show_revoked(address, outage, silent):
                self._storage.mark_revoked_notified(address.id, outage.uuid)
                report.revoked.append((address, outage))
        if any_new and decision.paused_until is not None:
            self._storage.set_paused_until(address.id, decision.paused_until)
