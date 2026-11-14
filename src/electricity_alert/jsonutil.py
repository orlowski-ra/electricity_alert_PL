"""Defensive JSON reading for unofficial APIs.

Every field is optional. Unknown fields are ignored. A field of the wrong type (e.g. a string
where a list is expected) raises :class:`BadFormat`, which callers turn into ``FormatError``.
Numbers are accepted where text is expected ("uuid": 123 -> "123").
"""

from __future__ import annotations

import json
from typing import Any


class BadFormat(Exception):
    pass


def loads(text: str) -> Any:
    try:
        return json.loads(text)
    except (ValueError, TypeError) as e:
        raise BadFormat(str(e)) from e


def obj(value: Any) -> dict[str, Any] | None:
    """A JSON object or null."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise BadFormat(f"expected object, got {type(value).__name__}")
    return value


def array(value: Any) -> list[Any] | None:
    """A JSON array or null. Elements may be null."""
    if value is None:
        return None
    if not isinstance(value, list):
        raise BadFormat(f"expected array, got {type(value).__name__}")
    return value


def text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise BadFormat("expected text, got boolean")
    if isinstance(value, (str, int, float)):
        return str(value)
    raise BadFormat(f"expected text, got {type(value).__name__}")


def boolean(value: Any) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise BadFormat(f"expected boolean, got {type(value).__name__}")
    return value


def integer(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise BadFormat(f"expected integer, got {type(value).__name__}")
    return value


def nonblank(value: str | None) -> str | None:
    """Trimmed text, or None when missing or blank."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
