from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api import router as api_router
from .cache import StateCache
from .db import HistoryDB
from .poller import Poller
from .settings import settings
from .ssh_client import OpenWrtSSH
from .ui import router as ui_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    from .vpnctl import VpnctlClient

    ssh = OpenWrtSSH(
        host=settings.openwrt_host,
        port=settings.openwrt_port,
        username=settings.openwrt_user,
        key_path=settings.ssh_key_path,
        known_hosts_path=settings.known_hosts_path,
        connect_timeout_sec=settings.ssh_connect_timeout_sec,
        command_timeout_sec=settings.ssh_command_timeout_sec,
    )
    app.state.vpnctl = VpnctlClient(ssh)
    app.state.cache = StateCache()
    app.state.db = HistoryDB(settings.db_path, settings.history_retention_days)
    await app.state.db.open()
    app.state.poller = Poller(
        app.state.vpnctl, app.state.cache, app.state.db, settings.poll_interval_sec
    )
    app.state.poller.start()
    yield
    await app.state.poller.stop()
    await app.state.db.close()


app = FastAPI(title="vpn_control_web", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
app.include_router(api_router)
app.include_router(ui_router)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
