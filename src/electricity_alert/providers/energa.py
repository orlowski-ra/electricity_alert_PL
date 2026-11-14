"""Energa-Operator (northern Poland).

Energa publishes one JSON document (~630 KB) with all outages of its network for ~2 weeks; we
download it at most every 3 hours and filter locally.

Address params (TERYT codes from the PGE nationwide dictionary): ``cityName`` (required),
``countyCode``, ``communeName``, ``communeType`` (all three missing = address typed by hand),
``street`` (optional).
"""

from __future__ import annotations

import os
import time as _time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from .. import jsonutil as j
from ..http import HttpClient
from ..models import Address, ApiResult, FormatError, Outage, ProviderId, Success, TimeWindow
from ..text import intervals_text, mentions_whole_word, street_key_word, strip_street_prefix
from .base import Log, no_log
from .tauron import parse_utc

BASE_URL = "https://energa-operator.pl/"
DOCUMENT_PATH = "__system/api/document/EXTERNAL_DOCUMENT-305647/"

CITY_SYM = "citySym"
CITY_NAME = "cityName"
COUNTY_CODE = "countyCode"
COMMUNE_NAME = "communeName"
COMMUNE_TYPE = "communeType"
STREET = "street"

TYPE_FAILURE = 1  # 2 = planned, 1 = failure

VOIVODESHIPS = frozenset(
    {
        "pomorskie",
        "warmińsko-mazurskie",
        "kujawsko-pomorskie",
        "mazowieckie",
        "wielkopolskie",
        "łódzkie",
        "zachodniopomorskie",
        "dolnośląskie",
    }
)


@dataclass(frozen=True)
class EnergaPlace:
    county_code: str | None
    commune_name: str | None
    commune_type_code: str | None
    city_name: str
    street: str | None = None


def area_name(commune_name: str, commune_type_code: str | None) -> str | None:
    """TERYT commune type -> area name used in Energa's document; None = unknown type."""
    suffix = {
        "01": "gmina miejska",
        "02": "gmina wiejska",
        "04": "miasto w gminie miejsko-wiejskiej",
        "05": "obszar wiejski w gminie miejsko-wiejskiej",
    }.get(commune_type_code or "")
    return f"{commune_name} {suffix}" if suffix else None


def shutdowns_of(document: Any) -> list[Any]:
    root = j.obj(document)
    body = j.obj(root.get("document")) if root else None
    payload = j.obj(body.get("payload")) if body else None
    shutdowns = j.array(payload.get("shutdowns")) if payload else None
    if shutdowns is None:
        raise j.BadFormat("no shutdowns in Energa document")
    return shutdowns


def to_outages(shutdowns: list[Any], place: EnergaPlace, window: TimeWindow, log: Log = no_log) -> list[Outage]:
    """An outage concerns the address when (1) ``counties`` contains the county code, (2) ``areas``
    contains the commune with its type, (3) ``message`` names the town as a whole word, and (4) if a
    street is chosen, ``message`` names its key word. Hand-typed addresses: only 3 and 4."""
    result: dict[str, Outage] = {}
    for raw in shutdowns:
        item = j.obj(raw)
        if item is None or j.integer(item.get("shutdownType")) == TYPE_FAILURE:
            continue
        if not _concerns(item, place):
            continue
        outage = _to_outage(item, window, log)
        if outage is not None:
            result.setdefault(outage.uuid, outage)
    return list(result.values())


def _concerns(item: dict[str, Any], place: EnergaPlace) -> bool:
    if place.county_code is not None:
        try:
            county = int(place.county_code)
        except ValueError:
            return False
        counties = [j.integer(c) for c in j.array(item.get("counties")) or []]
        if county not in counties:
            return False
    if place.commune_name is not None:
        areas = [a for a in (j.text(x) for x in j.array(item.get("areas")) or []) if a is not None]
        expected = area_name(place.commune_name, place.commune_type_code)
        if expected is not None:
            matches = any(a.lower() == expected.lower() for a in areas)
        else:
            matches = any(a.lower().startswith(place.commune_name.lower() + " ") for a in areas)
        if not matches:
            return False
    message = j.text(item.get("message"))
    if not mentions_whole_word(message, place.city_name):
        return False
    if place.street is None:
        return True
    return mentions_whole_word(message, street_key_word(place.street) or place.street)


def _to_outage(item: dict[str, Any], window: TimeWindow, log: Log) -> Outage | None:
    uuid = j.nonblank(j.text(item.get("guid")))
    if uuid is None:
        log("Energa shutdown without guid skipped")
        return None
    intervals = []
    for raw in j.array(item.get("hours")) or []:
        hours = j.obj(raw)
        if hours is not None:
            intervals.append((parse_utc(j.text(hours.get("fromDate")), log), parse_utc(j.text(hours.get("toDate")), log)))
    if not intervals:
        intervals = [(parse_utc(j.text(item.get("startDate")), log), parse_utc(j.text(item.get("endDate")), log))]
    in_window = [r for r in intervals if window.overlaps(r[0], r[1])]
    if not in_window:
        return None
    in_window.sort(key=lambda r: r[0] or r[1] or datetime.min)
    complete = [(a, b) for a, b in in_window if a is not None and b is not None]
    message = j.nonblank(j.text(item.get("message")))
    parts = [intervals_text(complete) if len(complete) > 1 else None, message]
    text = "\n".join(p for p in parts if p)
    return Outage(
        uuid=uuid,
        start_at=in_window[0][0],
        stop_at=in_window[-1][1],
        description=text or None,
    )


# --- Document cache ----------------------------------------------------------------------------


class DocumentCache(Protocol):
    def read(self) -> tuple[datetime, str] | None: ...

    def write(self, fetched_at: datetime, document: str) -> None: ...


class InMemoryDocumentCache:
    def __init__(self) -> None:
        self._value: tuple[datetime, str] | None = None

    def read(self) -> tuple[datetime, str] | None:
        return self._value

    def write(self, fetched_at: datetime, document: str) -> None:
        self._value = (fetched_at, document)


class FileDocumentCache:
    """File on disk: survives between CLI runs. Errors only mean more downloads, never a crash."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def read(self) -> tuple[datetime, str] | None:
        try:
            if not self._path.exists():
                return None
            mtime = datetime.fromtimestamp(self._path.stat().st_mtime, tz=UTC)
            return mtime, self._path.read_text(encoding="utf-8")
        except OSError:
            return None

    def write(self, fetched_at: datetime, document: str) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(document, encoding="utf-8")
            stamp = fetched_at.timestamp()
            os.utime(self._path, (stamp, stamp))
        except OSError:
            pass


def _utc_now() -> datetime:
    return datetime.now(UTC)


class EnergaProvider:
    id = ProviderId.ENERGA
    display_name = "Energa-Operator"

    def __init__(
        self,
        http: HttpClient,
        cache: DocumentCache | None = None,
        base_url: str = BASE_URL,
        clock: Callable[[], datetime] = _utc_now,
        max_age: timedelta = timedelta(hours=3),
        log: Log = no_log,
        fetch: Callable[[], ApiResult[str]] | None = None,
    ) -> None:
        self._http = http
        self._cache = cache or InMemoryDocumentCache()
        self._base = base_url
        self._clock = clock
        self._max_age = max_age
        self._log = log
        self._fetch = fetch or self._download

    def _download(self) -> ApiResult[str]:
        # The server ignores the number, but responses are cacheable for 28 days on the way:
        # a fixed number could return stale data, so we send the current time.
        version = int(_time.time())
        return self._http.get_text(f"{self._base}{DOCUMENT_PATH}{version}")

    def _document(self) -> ApiResult[list[Any]]:
        now = self._clock()
        cached = self._cache.read()
        if cached is not None and now - cached[0] < self._max_age:
            try:
                return Success(shutdowns_of(j.loads(cached[1])))
            except j.BadFormat:
                pass
        fetched = self._fetch()
        if not isinstance(fetched, Success):
            return fetched
        try:
            shutdowns = shutdowns_of(j.loads(fetched.data))
        except j.BadFormat:
            return FormatError()
        self._cache.write(now, fetched.data)
        return Success(shutdowns)

    def planned_outages(self, address: Address, window: TimeWindow) -> ApiResult[list[Outage]]:
        place = place_of(address)
        if place is None:
            return FormatError()
        document = self._document()
        if not isinstance(document, Success):
            return document
        try:
            return Success(to_outages(document.data, place, window, self._log))
        except j.BadFormat:
            return FormatError()


def place_of(address: Address) -> EnergaPlace | None:
    p = address.params
    city = j.nonblank(p.get(CITY_NAME))
    if city is None:
        return None
    street = strip_street_prefix(p.get(STREET) or "")
    return EnergaPlace(
        county_code=j.nonblank(p.get(COUNTY_CODE)),
        commune_name=j.nonblank(p.get(COMMUNE_NAME)),
        commune_type_code=j.nonblank(p.get(COMMUNE_TYPE)),
        city_name=city,
        street=street or None,
    )
