"""Stoen Operator (Warsaw only).

The API returns all planned outages (tens of entries); we filter by street locally.
Address params: ``street`` (e.g. "ul. Jana Miklaszewskiego").
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .. import jsonutil as j
from ..http import HttpClient
from ..models import Address, ApiResult, FormatError, Outage, ProviderId, Success, TimeWindow
from .base import Log, no_log

BASE_URL = "https://awaria.stoen.pl/"
SEARCH_PATH = "public/api/planned-outage/search/compressed-report"
PAGE_SIZE = 100
MAX_PAGES = 20

STREET = "street"

_PREFIX = re.compile(r"^((ul|al|pl|os|aleja|ulica|plac|osiedle)\.?\s+)+")
_QUOTES = re.compile(r"[„”\"«»]")
# Titles that one side abbreviates and the other writes in full.
_TITLES = frozenset(
    {
        "gen.",
        "generała",
        "ks.",
        "księdza",
        "kpt.",
        "kapitana",
        "płk.",
        "pułkownika",
        "św.",
        "świętego",
        "marsz.",
        "marszałka",
        "prof.",
        "profesora",
        "dr.",
        "doktora",
        "kard.",
        "kardynała",
        "bp.",
        "biskupa",
    }
)


def normalize_street(name: str) -> list[str]:
    text = _PREFIX.sub("", _QUOTES.sub(" ", name.lower()).strip())
    return [w for w in text.split() if w and w not in _TITLES]


def same_street(a: str | None, b: str | None) -> bool:
    """Same street when the normalised words are equal or the shorter name is the end of the
    longer one ("Miklaszewskiego" = "Jana Miklaszewskiego")."""
    if not a or not a.strip() or not b or not b.strip():
        return False
    x, y = normalize_street(a), normalize_street(b)
    if not x or not y:
        return False
    short, long = (x, y) if len(x) <= len(y) else (y, x)
    return long[-len(short) :] == short


def _parse(value: str | None, log: Log) -> datetime | None:
    if value is None or not value.strip():
        return None
    try:
        # Stoen appends random seconds ("08:00:41"): round down to the minute.
        return datetime.fromisoformat(value.strip().removesuffix("Z")).replace(second=0, microsecond=0, tzinfo=None)
    except ValueError:
        log(f"Stoen date not parsed: {value}")
        return None


def to_outages(items: list[Any], street: str, window: TimeWindow, log: Log = no_log) -> list[Outage]:
    result: dict[str, Outage] = {}
    for raw in items:
        item = j.obj(raw)
        if item is None:
            continue
        outage_id = j.integer(item.get("id"))
        if outage_id is None:
            log("Stoen outage without id skipped")
            continue
        addresses = [a for a in (j.obj(x) for x in j.array(item.get("addresses")) or []) if a is not None]
        matching = [a for a in addresses if same_street(j.text(a.get("streetName")), street)]
        if not matching:
            continue
        start = _parse(j.text(item.get("outageStart")), log)
        stop = _parse(j.text(item.get("outageEnd")), log)
        if not window.overlaps(start, stop):
            continue
        description = "; ".join(
            f"{(j.text(a.get('streetName')) or '').strip()} {(j.text(a.get('houseNumbers')) or '').strip()}".strip() for a in matching
        )
        uuid = f"stoen-{outage_id}"
        result.setdefault(uuid, Outage(uuid=uuid, start_at=start, stop_at=stop, description=description))
    return list(result.values())


class StoenProvider:
    id = ProviderId.STOEN
    display_name = "Stoen Operator"

    def __init__(self, http: HttpClient, base_url: str = BASE_URL, log: Log = no_log) -> None:
        self._http = http
        self._base = base_url
        self._log = log

    def all_planned_outages(self) -> ApiResult[list[Any]]:
        items: list[Any] = []
        for page in range(MAX_PAGES):
            result = self._http.post_json(self._base + SEARCH_PATH, {"page": {"limit": PAGE_SIZE, "offset": page * PAGE_SIZE}})
            if not isinstance(result, Success):
                return result
            if not isinstance(result.data, list):
                return FormatError()
            items.extend(result.data)
            if len(result.data) < PAGE_SIZE:
                break
        return Success(items)

    def planned_outages(self, address: Address, window: TimeWindow) -> ApiResult[list[Outage]]:
        street = j.nonblank(address.params.get(STREET))
        if street is None:
            return FormatError()
        result = self.all_planned_outages()
        if not isinstance(result, Success):
            return result
        try:
            return Success(to_outages(result.data, street, window, self._log))
        except j.BadFormat:
            return FormatError()
