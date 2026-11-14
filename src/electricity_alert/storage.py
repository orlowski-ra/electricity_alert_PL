"""SQLite storage: saved addresses and the "already notified" list."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .models import Address, NotifiedOutage, Outage, ProviderId

KEEP_NOTIFIED_DAYS = 30

_SCHEMA = """
CREATE TABLE IF NOT EXISTS address (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    label TEXT NOT NULL,
    params TEXT NOT NULL,
    paused_until TEXT
);
CREATE TABLE IF NOT EXISTS notified_outage (
    uuid TEXT NOT NULL,
    address_id INTEGER NOT NULL REFERENCES address(id) ON DELETE CASCADE,
    start_at TEXT,
    stop_at TEXT,
    notified_at TEXT NOT NULL,
    revoked_notified INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (uuid, address_id)
);
"""


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _from_iso(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class Storage:
    def __init__(self, path: Path | str = ":memory:") -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path))
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    # --- Addresses ---

    def add_address(self, provider: ProviderId, label: str, params: dict[str, str]) -> Address:
        with self._db:
            cursor = self._db.execute(
                "INSERT INTO address (provider, label, params) VALUES (?, ?, ?)",
                (provider.value, label, json.dumps(params, ensure_ascii=False)),
            )
        return Address(id=int(cursor.lastrowid or 0), provider=provider, label=label, params=dict(params))

    def addresses(self) -> list[Address]:
        rows = self._db.execute("SELECT * FROM address ORDER BY id").fetchall()
        result = []
        for row in rows:
            try:
                provider = ProviderId(row["provider"])
                params = json.loads(row["params"])
            except (ValueError, TypeError):
                continue
            if not isinstance(params, dict):
                continue
            result.append(
                Address(
                    id=row["id"],
                    provider=provider,
                    label=row["label"],
                    params={str(k): str(v) for k, v in params.items()},
                    paused_until=_from_iso(row["paused_until"]),
                )
            )
        return result

    def remove_address(self, address_id: int) -> bool:
        with self._db:
            cursor = self._db.execute("DELETE FROM address WHERE id = ?", (address_id,))
        return cursor.rowcount > 0

    def set_paused_until(self, address_id: int, until: datetime | None) -> None:
        with self._db:
            self._db.execute("UPDATE address SET paused_until = ? WHERE id = ?", (_iso(until), address_id))

    # --- Notified outages ---

    def notified_for(self, address_id: int) -> list[NotifiedOutage]:
        rows = self._db.execute(
            "SELECT uuid, revoked_notified, notified_at FROM notified_outage WHERE address_id = ?", (address_id,)
        ).fetchall()
        return [NotifiedOutage(r["uuid"], bool(r["revoked_notified"]), _from_iso(r["notified_at"])) for r in rows]

    def mark_notified(self, address_id: int, outage: Outage, now: datetime) -> bool:
        """Records a notification. True = first one about this outage, False = a reminder."""
        with self._db:
            cursor = self._db.execute(
                "INSERT OR IGNORE INTO notified_outage (uuid, address_id, start_at, stop_at, notified_at) VALUES (?, ?, ?, ?, ?)",
                (outage.uuid, address_id, _iso(outage.start_at), _iso(outage.stop_at), _iso(now)),
            )
            if cursor.rowcount > 0:
                return True
            self._db.execute(
                "UPDATE notified_outage SET notified_at = ?, start_at = ?, stop_at = ? WHERE uuid = ? AND address_id = ?",
                (_iso(now), _iso(outage.start_at), _iso(outage.stop_at), outage.uuid, address_id),
            )
        return False

    def mark_revoked_notified(self, address_id: int, uuid: str) -> None:
        with self._db:
            self._db.execute("UPDATE notified_outage SET revoked_notified = 1 WHERE uuid = ? AND address_id = ?", (uuid, address_id))

    def cleanup_old_notified(self, now: datetime) -> int:
        """Deletes entries notified more than KEEP_NOTIFIED_DAYS days ago."""
        cutoff = (now - timedelta(days=KEEP_NOTIFIED_DAYS)).astimezone(UTC)
        removed = 0
        rows = self._db.execute("SELECT uuid, address_id, notified_at FROM notified_outage").fetchall()
        with self._db:
            for row in rows:
                at = _from_iso(row["notified_at"])
                if at is None or at.astimezone(UTC) < cutoff:
                    self._db.execute("DELETE FROM notified_outage WHERE uuid = ? AND address_id = ?", (row["uuid"], row["address_id"]))
                    removed += 1
        return removed
