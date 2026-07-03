from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from .models import Server, VpnStatus


@dataclass
class Snapshot:
    status: VpnStatus | None = None
    servers: list[Server] = field(default_factory=list)
    fetched_at: float = 0.0
    stale: bool = True
    error: str | None = None


class StateCache:
    """In-memory snapshot of the router state, updated by the poller."""

    def __init__(self) -> None:
        self._snapshot = Snapshot()
        self._lock = asyncio.Lock()

    async def get(self) -> Snapshot:
        async with self._lock:
            return self._snapshot

    async def set(self, status: VpnStatus, servers: list[Server]) -> None:
        async with self._lock:
            self._snapshot = Snapshot(
                status=status, servers=servers, fetched_at=time.time(), stale=False, error=None
            )

    async def mark_stale(self, error: str) -> None:
        async with self._lock:
            old = self._snapshot
            self._snapshot = Snapshot(
                status=old.status,
                servers=old.servers,
                fetched_at=old.fetched_at,
                stale=True,
                error=error,
            )
