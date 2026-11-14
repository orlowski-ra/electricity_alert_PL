"""Outage providers (one module per distribution system operator)."""

from __future__ import annotations

from pathlib import Path

from ..http import HttpClient
from ..models import ProviderId
from .base import Log, OutageProvider, no_log
from .enea import EneaProvider
from .energa import EnergaProvider, FileDocumentCache
from .pge import PgeProvider
from .stoen import StoenProvider
from .tauron import TauronProvider


class ProviderRegistry:
    """All providers, in display order."""

    def __init__(self, providers: list[OutageProvider]) -> None:
        ids = [p.id for p in providers]
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate provider id")
        self.all = list(providers)
        self._by_id = {p.id: p for p in providers}

    def get(self, provider_id: ProviderId) -> OutageProvider | None:
        return self._by_id.get(provider_id)


def default_registry(http: HttpClient, cache_dir: Path | None = None, log: Log = no_log) -> ProviderRegistry:
    energa_cache = FileDocumentCache(cache_dir / "energa.json") if cache_dir else None
    return ProviderRegistry(
        [
            PgeProvider(http, log=log),
            TauronProvider(http, log=log),
            EneaProvider(http, log=log),
            EnergaProvider(http, cache=energa_cache, log=log),
            StoenProvider(http, log=log),
        ]
    )


__all__ = ["OutageProvider", "ProviderRegistry", "default_registry"]
