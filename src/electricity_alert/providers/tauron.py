"""TAURON Dystrybucja (southern Poland).

Address params: ``cityGAID`` and ``streetGAID`` (required), ``cityName`` and ``streetName`` (used
to narrow results), ``houseNo`` (optional; old addresses only).
JSON keys from this API are PascalCase.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .. import jsonutil as j
from ..http import HttpClient, map_success
from ..models import WARSAW, Address, ApiResult, FormatError, Outage, ProviderId, TimeWindow
from ..text import mentions_word_start, street_key_word
from .base import Log, no_log
from .pge import format_voivodeship

BASE_URL = "https://www.tauron-dystrybucja.pl/"

CITY_GAID = "cityGAID"
STREET_GAID = "streetGAID"
HOUSE_NO = "houseNo"
CITY_NAME = "cityName"
STREET_NAME = "streetName"

# Sent instead of a house number: the API requires one (HTTP 400 without it); "0" returns the
# list for the street's region, which we narrow by town and street name.
NO_HOUSE_NUMBER = "0"

TYPE_FAILURE = 2  # 1 = planned outage, 2 = failure

# Voivodeships served by Tauron (with partial ones). Its town dictionary covers all of Poland.
VOIVODESHIPS = frozenset(
    {"dolnośląskie", "opolskie", "śląskie", "małopolskie", "podkarpackie", "świętokrzyskie", "łódzkie", "lubuskie", "wielkopolskie"}
)


@dataclass(frozen=True)
class TauronCity:
    gaid: int
    name: str
    commune: str | None = None
    county: str | None = None
    voivodeship: str | None = None


@dataclass(frozen=True)
class TauronStreet:
    gaid: int
    name: str


def parse_utc(value: str | None, log: Log = no_log) -> datetime | None:
    """UTC from the API -> naive Polish local time."""
    if value is None or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        log(f"date not parsed: {value}")
        return None
    if parsed.tzinfo is None:
        log(f"date without zone: {value}")
        return None
    return parsed.astimezone(WARSAW).replace(tzinfo=None)


def to_cities(data: Any, log: Log = no_log) -> list[TauronCity]:
    cities = []
    for item in j.array(data) or []:
        dto = j.obj(item)
        if dto is None:
            continue
        gaid = j.integer(dto.get("GAID"))
        name = j.nonblank(j.text(dto.get("Name")))
        if gaid is None or name is None:
            log("Tauron city without GAID/Name skipped")
            continue
        province = j.nonblank(j.text(dto.get("ProvinceName")))
        cities.append(
            TauronCity(
                gaid=gaid,
                name=name,
                commune=j.nonblank(j.text(dto.get("CommuneName"))),
                county=j.nonblank(j.text(dto.get("DistrictName"))),
                voivodeship=format_voivodeship(province) if province else None,
            )
        )
    return cities


def to_streets(data: Any, log: Log = no_log) -> list[TauronStreet]:
    streets: dict[int, TauronStreet] = {}
    for item in j.array(data) or []:
        dto = j.obj(item)
        if dto is None:
            continue
        gaid = j.integer(dto.get("GAID"))
        # FullName has the prefix ("ul. Równoległa"); villages without streets: "  Czerwienne".
        name = j.nonblank(j.text(dto.get("FullName"))) or j.nonblank(j.text(dto.get("Name")))
        if gaid is None or name is None:
            log("Tauron street without GAID/Name skipped")
            continue
        streets.setdefault(gaid, TauronStreet(gaid, name))
    return list(streets.values())


def to_outages(
    data: Any,
    city_name: str | None,
    window: TimeWindow,
    street_name: str | None = None,
    log: Log = no_log,
) -> list[Outage]:
    """The API returns outages for a whole region, not the address. We narrow it down:

    - if the API recognised the house number (``AddressPoint``) and the outage lists address
      points, only outages that contain ours,
    - otherwise only outages whose description mentions our town and, in a town with streets,
      our street.

    Only outages overlapping ``window`` (the server filters by day, not hour); one ``OutageId``
    may appear several times with different days and is merged after filtering.
    """
    dto = j.obj(data)
    if dto is None:
        raise j.BadFormat("expected object")
    point = j.obj(dto.get("AddressPoint"))
    address_point = j.integer(point.get("AddressPointId")) if point else None
    result: dict[str, Outage] = {}
    for raw in j.array(dto.get("OutageItems")) or []:
        item = j.obj(raw)
        if item is None:
            continue
        if j.integer(item.get("TypeId")) == TYPE_FAILURE:
            continue
        uuid = j.nonblank(j.text(item.get("OutageId")))
        message = j.text(item.get("Message"))
        points = [p for p in (j.integer(x) for x in j.array(item.get("AddressPointIds")) or []) if p is not None]
        if uuid is None:
            log("Tauron outage without OutageId skipped")
            continue
        outage = Outage(
            uuid=uuid,
            start_at=parse_utc(j.text(item.get("StartDate")), log),
            stop_at=parse_utc(j.text(item.get("EndDate")), log),
            revoked=j.boolean(item.get("IsActive")) is False,
            description=j.nonblank(message),
        )
        if address_point is not None and points:
            concerns = address_point in points
        else:
            concerns = mentions_city(message, city_name) and mentions_street(message, street_name, city_name)
        if concerns and window.overlaps(outage.start_at, outage.stop_at):
            result.setdefault(uuid, outage)
    return list(result.values())


def mentions_city(message: str | None, city_name: str | None) -> bool:
    """Town at the start of a word, with declension ("Kraków" -> "w Krakowie").
    No town name (old saved address) -> no filter."""
    name = j.nonblank(city_name)
    if name is None:
        return True
    return mentions_word_start(message, name.lower())


_WORD_SEPARATORS = re.compile(r"[\s\"„”-]+")


def mentions_street(message: str | None, street_name: str | None, city_name: str | None) -> bool:
    """Description mentions the street's key word ("gen. Józefa Hallera" -> "Hallera"). A hyphenated
    name matches by any part. No street, or a village where street == town -> no filter."""
    name = j.nonblank(street_name)
    if name is None:
        return True
    if city_name is not None and name.lower() == city_name.strip().lower():
        return True
    key = street_key_word(name)
    parts = [p for p in _WORD_SEPARATORS.split(key.lower())] if key else []
    parts = [p for p in parts if len(p) >= 3]
    if not parts:
        return True
    return any(mentions_word_start(message, p) for p in parts)


class TauronProvider:
    id = ProviderId.TAURON
    display_name = "TAURON Dystrybucja"

    def __init__(self, http: HttpClient, base_url: str = BASE_URL, log: Log = no_log) -> None:
        self._http = http
        self._base = base_url
        self._log = log

    def search_cities(self, query: str) -> ApiResult[list[TauronCity]]:
        result = self._http.get_json(self._base + "waapi/enum/geo/cities", {"partName": query.strip()})
        return map_success(result, lambda d: to_cities(d, self._log))

    def only_one_street(self, city_gaid: int) -> ApiResult[TauronStreet | None]:
        """A town with a single "street" (usually a village) -> that street; more -> None."""
        result = self._http.get_json(self._base + "waapi/enum/geo/onlyonestreet", {"ownerGAID": city_gaid})
        return map_success(result, lambda d: _single(to_streets(d, self._log)))

    def search_streets(self, query: str, city_gaid: int) -> ApiResult[list[TauronStreet]]:
        result = self._http.get_json(self._base + "waapi/enum/geo/streets", {"partName": query.strip(), "ownerGAID": city_gaid})
        return map_success(result, lambda d: to_streets(d, self._log))

    def planned_outages(self, address: Address, window: TimeWindow) -> ApiResult[list[Outage]]:
        params = address.params
        city_gaid = _to_int(params.get(CITY_GAID))
        street_gaid = _to_int(params.get(STREET_GAID))
        if city_gaid is None or street_gaid is None:
            return FormatError()
        house_no = j.nonblank(params.get(HOUSE_NO)) or NO_HOUSE_NUMBER
        # The server handles date ranges inconsistently (about half the answers for a window ending
        # on a day skip that day). "Day before – day after" gave full results; exact filter is ours.
        day_from = (window.start.date() - timedelta(days=1)).isoformat()
        day_to = (window.end.date() + timedelta(days=1)).isoformat()
        result = self._http.get_json(
            self._base + "waapi/outages/address",
            {
                "cityGAID": city_gaid,
                "streetGAID": street_gaid,
                "houseNo": house_no,
                "fromDate": f"{day_from}T00:00:00.000Z",
                "toDate": f"{day_to}T23:59:59.999Z",
            },
        )
        return map_success(
            result,
            lambda d: to_outages(d, params.get(CITY_NAME), window, params.get(STREET_NAME), self._log),
        )


def _single(items: list[TauronStreet]) -> TauronStreet | None:
    return items[0] if len(items) == 1 else None


def _to_int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None
