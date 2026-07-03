from __future__ import annotations

import asyncio
import logging

from .cache import StateCache
from .db import HistoryDB
from .vpnctl import VpnctlClient, VpnctlError

log = logging.getLogger(__name__)

_PRUNE_EVERY_TICKS = 240  # ~1h at 15s interval


class Poller:
    """
    Background task: periodically pulls status+list from the router into the
    in-memory cache and appends new check results to the history DB. UI reads
    never hit SSH synchronously.
    """

    def __init__(
        self,
        client: VpnctlClient,
        cache: StateCache,
        db: HistoryDB,
        interval_sec: float,
    ):
        self._client = client
        self._cache = cache
        self._db = db
        self._interval = interval_sec
        self._task: asyncio.Task | None = None
        self._wakeup = asyncio.Event()

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="vpnctl-poller")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def poke(self) -> None:
        """Request an immediate poll (after a mutation)."""
        self._wakeup.set()

    async def _loop(self) -> None:
        tick = 0
        backoff = self._interval
        while True:
            try:
                status = await self._client.status()
                servers = (await self._client.list_servers()).servers
                await self._cache.set(status, servers)
                await self._db.record_checks(servers)
                backoff = self._interval
            except (VpnctlError, Exception) as exc:  # noqa: BLE001 - poller must survive
                log.warning("poll failed: %s", exc)
                await self._cache.mark_stale(str(exc))
                backoff = min(backoff * 2, 120.0)

            tick += 1
            if tick % _PRUNE_EVERY_TICKS == 0:
                try:
                    await self._db.prune()
                except Exception as exc:  # noqa: BLE001
                    log.warning("history prune failed: %s", exc)

            # When busy (e.g. background check on the router), poll faster to
            # surface fresh latency values sooner.
            snapshot = await self._cache.get()
            delay = backoff
            if snapshot.status is not None and snapshot.status.busy:
                delay = min(delay, 5.0)

            self._wakeup.clear()
            try:
                await asyncio.wait_for(self._wakeup.wait(), timeout=delay)
            except TimeoutError:
                pass
