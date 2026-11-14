"""PGE Dystrybucja.

Two hosts: ``falcon.gkpge.pl`` (TERYT town and street dictionary) and ``power-outage.gkpge.pl``
(outages). Address params: ``citySym`` (required), ``streetSym`` (optional).
The TERYT dictionary also covers the whole country (``nationwide=True``) and is used to build
addresses for Energa.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from .. import jsonutil as j
from ..http import HttpClient, map_success
from ..models import DATE_FORMAT, Address, ApiResult, FormatError, Outage, ProviderId, Success, TimeWindow
from ..text import intervals_text
from .base import Log, no_log

FALCON_BASE_URL = "https://falcon.gkpge.pl/"
OUTAGE_BASE_URL = "https://power-outage.gkpge.pl/"

PARAM_CITY_SYM = "citySym"
PARAM_STREET_SYM = "streetSym"

# TERYT commune types: 01 urban, 02 rural, 04/05 town/rural part of an urban-rural commune,
# 09 district of a big city (Łódź-Bałuty).
URBAN_TYPE = "01"
DISTRICT_TYPE = "09"


@dataclass(frozen=True)
class PgeCity:
    city_sym: str
    name: str
    commune: str | None = None
    county: str | None = None
    voivodeship: str | None = None
    county_code: str | None = None
    commune_type_code: str | None = None


@dataclass(frozen=True)
class PgeStreet:
    street_sym: str
    name: str
    # Cities with districts: citySym and name of the district the street belongs to.
    city_sym: str | None = None
    district: str | None = None

    @property
    def key(self) -> str:
        return f"{self.city_sym or ''}:{self.street_sym}"


def format_voivodeship(raw: str) -> str:
    """ "MAZOWIECKIE" -> "Mazowieckie"."""
    lower = raw.strip().lower()
    return lower[:1].upper() + lower[1:]


def parse_local(value: str | None, log: Log = no_log) -> datetime | None:
    if value is None or not value.strip():
        return None
    try:
        return datetime.strptime(value.strip(), DATE_FORMAT)
    except ValueError:
        log(f"PGE date not parsed: {value}")
        return None


# --- Mapping (pure) ----------------------------------------------------------------------------


def to_cities(data: Any, log: Log = no_log) -> list[PgeCity]:
    cities = []
    for item in j.array(data) or []:
        dto = j.obj(item)
        if dto is None:
            continue
        sym = j.nonblank(j.text(dto.get("citySym")))
        name = j.nonblank(j.text(dto.get("cityName")))
        if sym is None or name is None:
            log("PGE city without citySym/cityName skipped")
            continue
        voivodeship = j.text(dto.get("voivodeshipName"))
        cities.append(
            PgeCity(
                city_sym=sym,
                name=name,
                commune=j.text(dto.get("communeName")),
                county=j.text(dto.get("countyName")),
                voivodeship=format_voivodeship(voivodeship) if voivodeship else None,
                county_code=j.nonblank(j.text(dto.get("countyCode"))),
                commune_type_code=j.nonblank(j.text(dto.get("communeTypeCode"))),
            )
        )
    return cities


def to_streets(data: Any, log: Log = no_log) -> list[PgeStreet]:
    streets = []
    for item in j.array(data) or []:
        dto = j.obj(item)
        if dto is None:
            continue
        sym = j.nonblank(j.text(dto.get("symUl")))
        name = j.text(dto.get("name"))
        if sym is None or name is None or not name.strip():
            log("PGE street without symUl/name skipped")
            continue
        streets.append(PgeStreet(street_sym=sym, name=name))
    return streets


def to_outages(
    data: Any,
    city_sym: str,
    street_sym: str | None = None,
    window: TimeWindow | None = None,
    log: Log = no_log,
) -> list[Outage]:
    """One PGE outage can cover several towns/streets: ``numbers`` keeps only the house numbers of
    addresses matching ``city_sym`` (and ``street_sym``), or all of them when none matches.

    ``start_at``/``stop_at`` from the API are only an envelope (8.10 and 16.10 -> 8.10–16.10), so
    with a ``window`` we use the real ``dateRanges`` that overlap it.
    """
    outages = []
    for item in j.array(data) or []:
        dto = j.obj(item)
        if dto is None:
            continue
        uuid = j.nonblank(j.text(dto.get("uuid")))
        start_raw = j.text(dto.get("startAt"))
        stop_raw = j.text(dto.get("stopAt"))
        revoked = j.boolean(dto.get("revoked")) or False
        addresses = [a for a in (j.obj(x) for x in j.array(dto.get("addresses")) or []) if a is not None]
        ranges_raw = j.array(dto.get("dateRanges")) or []
        if uuid is None:
            log("PGE outage without uuid skipped")
            continue
        matching = [a for a in addresses if _matches(a, city_sym, street_sym)]
        numbers = []
        for address in matching or addresses:
            value = j.nonblank(j.text(address.get("numbers")))
            if value:
                numbers.append(value)
        ranges = _ranges_in(ranges_raw, window, log) if window else []
        outages.append(
            Outage(
                uuid=uuid,
                numbers=tuple(numbers),
                start_at=ranges[0][0] if ranges else parse_local(start_raw, log),
                stop_at=ranges[-1][1] if ranges else parse_local(stop_raw, log),
                revoked=revoked,
                description=intervals_text(ranges) if len(ranges) > 1 else None,
            )
        )
    return outages


def _matches(address: dict[str, Any], city_sym: str, street_sym: str | None) -> bool:
    teryt = j.obj(address.get("teryt"))
    if teryt is None or j.text(teryt.get("citySym")) != city_sym:
        return False
    return street_sym is None or j.text(teryt.get("streetSymUl")) == street_sym


def _ranges_in(raw: list[Any], window: TimeWindow, log: Log) -> list[tuple[datetime, datetime]]:
    ranges = []
    for item in raw:
        dto = j.obj(item)
        if dto is None:
            continue
        start = parse_local(j.text(dto.get("startAt")), log)
        stop = parse_local(j.text(dto.get("stopAt")), log)
        if start and stop and stop >= window.start and start <= window.end:
            ranges.append((start, stop))
    return sorted(ranges, key=lambda r: r[0])


def without_city_parts(cities: list[PgeCity]) -> list[PgeCity]:
    """Drops city neighbourhoods ("Retkinia" in Łódź-Polesie): TERYT lists them as separate towns
    without streets, but outages are assigned to the city/district, so they would never match."""
    return [
        c
        for c in cities
        if not (c.commune_type_code in (URBAN_TYPE, DISTRICT_TYPE) and c.commune is not None and c.commune.lower() != c.name.lower())
    ]


# --- Provider ----------------------------------------------------------------------------------


class PgeProvider:
    id = ProviderId.PGE
    display_name = "PGE Dystrybucja"

    def __init__(
        self,
        http: HttpClient,
        falcon_base_url: str = FALCON_BASE_URL,
        outage_base_url: str = OUTAGE_BASE_URL,
        log: Log = no_log,
    ) -> None:
        self._http = http
        self._falcon = falcon_base_url
        self._outages = outage_base_url
        self._log = log

    def search_cities(self, query: str, nationwide: bool = False) -> ApiResult[list[PgeCity]]:
        """Towns by name. ``nationwide`` searches all of Poland, not only the PGE area."""
        scope = None if nationwide else "PgeDystrybucja"
        result = self._http.get_json(self._falcon + "public/teryt-city", {"cityName": query.strip(), "scope": scope})
        return map_success(result, lambda d: without_city_parts(to_cities(d, self._log)))

    def streets(self, city_sym: str) -> ApiResult[list[PgeStreet]]:
        result = self._http.get_json(self._falcon + "public/street", {"citySym": city_sym})
        return map_success(result, lambda d: to_streets(d, self._log))

    def streets_with_districts(self, city_sym: str, city_name: str, nationwide: bool = False) -> ApiResult[list[PgeStreet]]:
        """Streets of a town. Cities with districts (Łódź, Kraków, Poznań, Wrocław) have no streets
        of their own in TERYT; streets belong to districts ("Łódź-Bałuty") and outages are
        published per district. Then we merge the streets of all districts."""
        own = self.streets(city_sym)
        if not isinstance(own, Success) or own.data or not city_name.strip():
            return own
        search = self.search_cities(city_name, nationwide)
        if not isinstance(search, Success):
            return search
        county_code = next((c.county_code for c in search.data if c.city_sym == city_sym), None)
        districts = [
            c
            for c in search.data
            if c.commune_type_code == DISTRICT_TYPE
            and c.name.startswith(f"{city_name}-")
            and (county_code is None or c.county_code == county_code)
        ]
        if not districts:
            return own
        merged: list[PgeStreet] = []
        for district in districts:
            result = self.streets(district.city_sym)
            if not isinstance(result, Success):
                return result
            short = district.name.removeprefix(f"{city_name}-")
            merged.extend(replace(s, city_sym=district.city_sym, district=short) for s in result.data)
        return Success(sorted(merged, key=lambda s: polish_sort_key(s.name)))

    def planned_outages(self, address: Address, window: TimeWindow) -> ApiResult[list[Outage]]:
        city_sym = j.nonblank(address.params.get(PARAM_CITY_SYM))
        if city_sym is None:
            # Broken saved address: do not query the API blindly.
            return FormatError()
        street_sym = j.nonblank(address.params.get(PARAM_STREET_SYM))
        result = self._http.get_json(
            self._outages + "api/power-outage",
            {
                "type": "teryt",
                "startAtTo": window.end_text,
                "stopAtFrom": window.start_text,
                "citySym": city_sym,
                "streetSym": street_sym,
                "types[]": 2,
            },
        )
        return map_success(result, lambda d: to_outages(d, city_sym, street_sym, window, self._log))


_PL_ORDER = "aąbcćdeęfghijklłmnńoóprsśtuwyzźż"


def polish_sort_key(text: str) -> tuple[int, ...]:
    """Polish alphabetical order (ą after a, ł after l, ...); spaces and punctuation first."""
    key = []
    for ch in text.lower():
        index = _PL_ORDER.find(ch)
        if index >= 0:
            key.append(1000 + index)
        elif ch.isalpha():
            key.append(2000 + ord(ch))
        else:
            key.append(ord(ch) if ord(ch) < 1000 else 3000 + ord(ch))
    return tuple(key)
