from __future__ import annotations

import logging
import os
import time

import aiosqlite

from .models import HistoryPoint, Server

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS check_history (
    ts INTEGER NOT NULL,
    server_id TEXT NOT NULL,
    status TEXT NOT NULL,
    latency_ms INTEGER
);
CREATE INDEX IF NOT EXISTS idx_history_server_ts ON check_history (server_id, ts);
CREATE INDEX IF NOT EXISTS idx_history_ts ON check_history (ts);
"""


class HistoryDB:
    """SQLite store for latency-check history, fed by the poller."""

    def __init__(self, path: str, retention_days: int):
        self._path = path
        self._retention_sec = retention_days * 86400
        self._db: aiosqlite.Connection | None = None
        # Track last stored (server_id -> last_checked) to only append new checks.
        self._last_seen: dict[str, int] = {}

    async def open(self) -> None:
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        await self._db.executescript(_SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def record_checks(self, servers: list[Server]) -> None:
        """Append rows for servers whose last_checked advanced since last poll."""
        if self._db is None:
            return
        rows = []
        for s in servers:
            if s.last_checked is None:
                continue
            if self._last_seen.get(s.id) == s.last_checked:
                continue
            self._last_seen[s.id] = s.last_checked
            rows.append((s.last_checked, s.id, s.status, s.latency_ms))
        if not rows:
            return
        await self._db.executemany(
            "INSERT INTO check_history (ts, server_id, status, latency_ms) VALUES (?, ?, ?, ?)",
            rows,
        )
        await self._db.commit()

    async def prune(self) -> None:
        if self._db is None:
            return
        cutoff = int(time.time()) - self._retention_sec
        await self._db.execute("DELETE FROM check_history WHERE ts < ?", (cutoff,))
        await self._db.commit()

    async def history(self, server_id: str | None, hours: int) -> list[HistoryPoint]:
        if self._db is None:
            return []
        since = int(time.time()) - hours * 3600
        if server_id:
            cursor = await self._db.execute(
                "SELECT ts, server_id, status, latency_ms FROM check_history"
                " WHERE server_id = ? AND ts >= ? ORDER BY ts",
                (server_id, since),
            )
        else:
            cursor = await self._db.execute(
                "SELECT ts, server_id, status, latency_ms FROM check_history"
                " WHERE ts >= ? ORDER BY ts",
                (since,),
            )
        rows = await cursor.fetchall()
        return [
            HistoryPoint(ts=r[0], server_id=r[1], status=r[2], latency_ms=r[3]) for r in rows
        ]
