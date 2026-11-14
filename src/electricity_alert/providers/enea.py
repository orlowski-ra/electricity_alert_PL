"""Enea Operator (western Poland). No API: the search form of the outage page returns HTML.

Address params: ``city`` (required), ``street`` (optional, typed by the user or from TERYT).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from ..http import HttpClient
from ..models import Address, ApiResult, FormatError, Outage, ProviderId, Success, TimeWindow
from ..text import intervals_text, mentions_whole_word, street_key_word, strip_street_prefix
from .base import Log, no_log

BASE_URL = "https://wylaczenia.operator.enea.pl/"

CITY = "city"
STREET = "street"

VOIVODESHIPS = frozenset({"zachodniopomorskie", "lubuskie", "wielkopolskie", "kujawsko-pomorskie", "pomorskie"})

_CONTAINER = 'class="unpl_cont"'
_BLOCK_START = re.compile(r'<div class="unpl block')
_UUID = re.compile(r"<!--\s*([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\s*-->")
_HOURS_COMMENT = re.compile(r"<!--[^>]*?w godz\.([^>]*?)-->")
_INTERVAL = re.compile(r"(\d{4}-\d\d-\d\d \d\d:\d\d)\s*-\s*(\d{4}-\d\d-\d\d \d\d:\d\d)")
_DESCRIPTION_START = '<p class="description">'
_DESCRIPTION = re.compile(r'<p class="description">([\s\S]*?)</p>')
_REVOKED = re.compile(r"WYŁĄCZENIE\s+ODWOŁANE", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"\s+")
_ENTITIES = {
    "&nbsp;": " ",
    "&amp;": "&",
    "&quot;": '"',
    "&#39;": "'",
    "&apos;": "'",
    "&lt;": "<",
    "&gt;": ">",
    "&oacute;": "ó",
    "&Oacute;": "Ó",
}


@dataclass(frozen=True)
class EneaBlock:
    """One outage from the page; a multi-day outage has several intervals."""

    id: str
    intervals: list[tuple[datetime, datetime]] = field(default_factory=list)
    description: str | None = None
    revoked: bool = False

    def to_outage(self, window: TimeWindow) -> Outage | None:
        """Outage for ``window`` with the intervals overlapping it; None = does not concern it.
        No dates at all (page format changed) -> outage without hours, so the alert is not lost."""
        in_window = sorted([(a, b) for a, b in self.intervals if b >= window.start and a <= window.end], key=lambda r: r[0])
        if self.intervals and not in_window:
            return None
        parts = [intervals_text(in_window) if len(in_window) > 1 else None, self.description]
        text = "\n".join(p for p in parts if p)
        return Outage(
            uuid=self.id,
            start_at=in_window[0][0] if in_window else None,
            stop_at=in_window[-1][1] if in_window else None,
            revoked=self.revoked,
            description=text or None,
        )


def clean_text(html: str) -> str:
    text = _TAG.sub(" ", html)
    for entity, char in _ENTITIES.items():
        text = text.replace(entity, char)
    return _SPACES.sub(" ", text).strip()


def parse(html: str, log: Log = no_log) -> list[EneaBlock] | None:
    """All outages on the page; None = the page does not look like Enea's outage page."""
    start = html.find(_CONTAINER)
    if start < 0:
        return None
    blocks: dict[str, EneaBlock] = {}
    for chunk in _BLOCK_START.split(html[start:])[1:]:
        block = _parse_block(chunk, log)
        if block is not None:
            blocks.setdefault(block.id, block)
    return list(blocks.values())


def _parse_block(block: str, log: Log) -> EneaBlock | None:
    found = _UUID.search(block)
    if found is None:
        log("Enea block without id skipped")
        return None
    block_id = found.group(1)
    hours = _HOURS_COMMENT.search(block)
    intervals = []
    for match in _INTERVAL.finditer(hours.group(1) if hours else ""):
        start = _parse_date(match.group(1), log)
        stop = _parse_date(match.group(2), log)
        if start and stop:
            intervals.append((start, stop))
    if not intervals:
        log(f"Enea block {block_id} without dates")
    description = _DESCRIPTION.search(block)
    cleaned = clean_text(description.group(1)) if description else ""
    # Only the part before the description: the word "odwołane" inside it must not count.
    head = block.split(_DESCRIPTION_START, 1)[0]
    return EneaBlock(
        id=block_id,
        intervals=intervals,
        description=cleaned or None,
        revoked=_REVOKED.search(head) is not None,
    )


def _parse_date(value: str, log: Log) -> datetime | None:
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M")
    except ValueError:
        log(f"Enea date not parsed: {value}")
        return None


def mentions_town(description: str | None, town: str) -> bool:
    """Enea's search is loose ("Poznań" also finds "ul. Poznańska" in Chociwel), so the
    description must contain the town as a whole word."""
    return mentions_whole_word(description, town)


class EneaProvider:
    id = ProviderId.ENEA
    display_name = "Enea Operator"

    def __init__(self, http: HttpClient, base_url: str = BASE_URL, log: Log = no_log) -> None:
        self._http = http
        self._base = base_url
        self._log = log

    def search_page(self, city: str, street: str) -> ApiResult[str]:
        return self._http.request_text(
            "POST",
            self._base,
            data={"page": "unpl", "unpl_id": "", "unpl_city": city, "unpl_street": street},
        )

    def planned_outages(self, address: Address, window: TimeWindow) -> ApiResult[list[Outage]]:
        city = (address.params.get(CITY) or "").strip()
        if not city:
            return FormatError()
        # Enea writes "ul. Żeromskiego", TERYT "Stefana Żeromskiego": send only the key word.
        street = strip_street_prefix(address.params.get(STREET) or "")
        street = street_key_word(street) or street
        page = self.search_page(city, street)
        if not isinstance(page, Success):
            return page
        blocks = parse(page.data, self._log)
        if blocks is None:
            return FormatError()
        outages = []
        for block in blocks:
            if mentions_town(block.description, city):
                outage = block.to_outage(window)
                if outage is not None:
                    outages.append(outage)
        return Success(outages)
