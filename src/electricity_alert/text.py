"""Polish text helpers: loose search, street key words, outage descriptions."""

from __future__ import annotations

import re
from datetime import datetime

from .models import Outage

_FOLD = str.maketrans("ąćęłńóśźż", "acelnoszz")


def fold(text: str) -> str:
    """Lower case without Polish diacritics: "Łużycka" -> "luzycka"."""
    return text.lower().translate(_FOLD)


def contains_loosely(text: str, query: str) -> bool:
    return fold(query) in fold(text)


# --- Street names ------------------------------------------------------------------------------

_STREET_PREFIX = re.compile(r"^((ul|al|pl|os|aleja|ulica|plac|osiedle|rondo|skwer|bulwar|park)\.?\s+)+", re.IGNORECASE)
_PSEUDONYM = re.compile(r"\bps\.?\s*[\"„“”«][^\"„“”«»]*[\"“”»]", re.IGNORECASE)
_QUOTES = re.compile(r"[\"„“”«»]")


def street_key_word(street: str | None) -> str | None:
    """Last meaningful word of a street name.

    Operators shorten names ("ul. Żeromskiego" instead of "ul. Stefana Żeromskiego"), so we
    compare only the key word, usually the surname: "Jana Pawła II" -> "Pawła",
    "23 Lutego" -> "Lutego". ``None`` when nothing is left.
    """
    text = (street or "").strip()
    text = _STREET_PREFIX.sub("", text)
    text = _PSEUDONYM.sub(" ", text)
    text = _QUOTES.sub(" ", text).strip()
    candidates = [w.strip(",;") for w in text.split()]
    for word in reversed(candidates):
        if len(word) >= 3 and not word.endswith(".") and any(c.isalpha() for c in word):
            return word
    return None


_SIMPLE_PREFIX = re.compile(r"^(ul|al|pl|os)\.?\s+", re.IGNORECASE)


def strip_street_prefix(street: str) -> str:
    """ "ul. Górnicza" -> "Górnicza"."""
    return _SIMPLE_PREFIX.sub("", street.strip()).strip()


def mentions_whole_word(text: str | None, phrase: str) -> bool:
    """``phrase`` occurs in ``text`` as whole word(s), case-insensitive ("Nowa Wieś")."""
    needle = phrase.strip().lower()
    if not needle:
        return True
    if text is None:
        return False
    haystack = text.lower()
    index = haystack.find(needle)
    while index >= 0:
        before = haystack[index - 1] if index > 0 else None
        end = index + len(needle)
        after = haystack[end] if end < len(haystack) else None
        if (before is None or not before.isalpha()) and (after is None or not after.isalpha()):
            return True
        index = haystack.find(needle, index + 1)
    return False


def mentions_word_start(text: str | None, name: str) -> bool:
    """Stem of ``name`` at the start of a word, to catch declension ("Kraków" -> "w Krakowie")."""
    if text is None:
        return False
    haystack = text.lower()
    stem = name[:-2] if len(name) >= 6 else name
    index = haystack.find(stem)
    while index >= 0:
        if index == 0 or not haystack[index - 1].isalpha():
            return True
        index = haystack.find(stem, index + 1)
    return False


# --- Outage text -------------------------------------------------------------------------------


def _date_time(value: datetime) -> str:
    return f"{value.day}.{value:%m.%Y %H:%M}"


def _day_time(value: datetime) -> str:
    return f"{value.day}.{value:%m %H:%M}"


def numbers_text(numbers: tuple[str, ...] | list[str]) -> str:
    return "; ".join(numbers)


def range_text(start: datetime | None, stop: datetime | None) -> str:
    """ "14.10.2026 08:00 – 16:00"; a different end day shows the full date; missing -> "?"."""
    start_text = _date_time(start) if start else "?"
    if stop is None:
        stop_text = "?"
    elif start is not None and start.date() == stop.date():
        stop_text = f"{stop:%H:%M}"
    else:
        stop_text = _date_time(stop)
    return f"{start_text} – {stop_text}"


def intervals_text(ranges: list[tuple[datetime, datetime]]) -> str:
    """Several breaks of one outage: "08:00–10:00, 12:00–14:00", with dates if on several days."""
    days = {d for start, stop in ranges for d in (start.date(), stop.date())}
    multi_day = len(days) > 1
    parts = []
    for start, stop in ranges:
        if not multi_day:
            parts.append(f"{start:%H:%M}–{stop:%H:%M}")
        elif start.date() == stop.date():
            parts.append(f"{_day_time(start)}–{stop:%H:%M}")
        else:
            parts.append(f"{_day_time(start)}–{_day_time(stop)}")
    return ", ".join(parts)


def outage_body(outage: Outage) -> str:
    """Notification body: numbers, description and time, each on its own line."""
    lines = [numbers_text(outage.numbers), outage.description or "", range_text(outage.start_at, outage.stop_at)]
    return "\n".join(line for line in lines if line.strip())
