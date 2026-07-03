from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from .models import (
    SERVER_ID_PATTERN,
    ModeRequest,
    RefreshIntervalRequest,
    SelectRequest,
    SubscriptionUrlRequest,
)
from .ssh_client import CommandTimeout, SSHError
from .vpnctl import VpnctlBusy, VpnctlError

router = APIRouter(prefix="/api")


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, VpnctlBusy):
        return HTTPException(status_code=409, detail={"error": "busy", "message": exc.message})
    if isinstance(exc, VpnctlError):
        return HTTPException(status_code=502, detail={"error": exc.code, "message": exc.message})
    if isinstance(exc, (SSHError, CommandTimeout)):
        return HTTPException(status_code=502, detail={"error": "ssh", "message": str(exc)})
    raise exc


@router.get("/status")
async def get_status(request: Request):
    snapshot = await request.app.state.cache.get()
    return {
        "status": snapshot.status.model_dump() if snapshot.status else None,
        "fetched_at": snapshot.fetched_at,
        "stale": snapshot.stale,
        "error": snapshot.error,
    }


@router.get("/servers")
async def get_servers(request: Request):
    snapshot = await request.app.state.cache.get()
    return {
        "servers": [s.model_dump() for s in snapshot.servers],
        "fetched_at": snapshot.fetched_at,
        "stale": snapshot.stale,
    }


@router.get("/logs")
async def get_logs(request: Request, limit: int = Query(default=50, ge=1, le=500)):
    try:
        return (await request.app.state.vpnctl.logs(limit)).model_dump()
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc


@router.get("/history")
async def get_history(
    request: Request,
    server_id: str | None = Query(default=None, pattern=SERVER_ID_PATTERN),
    hours: int = Query(default=24, ge=1, le=24 * 30),
):
    points = await request.app.state.db.history(server_id, hours)
    return {"points": [p.model_dump() for p in points]}


@router.post("/refresh")
async def post_refresh(request: Request, force: bool = Query(default=False)):
    try:
        result = await request.app.state.vpnctl.refresh(force=force)
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc
    request.app.state.poller.poke()
    return result


@router.post("/subscription/url")
async def post_subscription_url(request: Request, body: SubscriptionUrlRequest):
    try:
        result = await request.app.state.vpnctl.set_subscription_url(body.url)
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc
    request.app.state.poller.poke()
    return result


@router.post("/subscription/interval")
async def post_subscription_interval(request: Request, body: RefreshIntervalRequest):
    try:
        result = await request.app.state.vpnctl.set_refresh_interval(body.interval_sec)
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc
    request.app.state.poller.poke()
    return result


@router.post("/check")
async def post_check(
    request: Request,
    server_id: str | None = Query(default=None, pattern=SERVER_ID_PATTERN),
):
    try:
        result = await request.app.state.vpnctl.check(server_id)
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc
    request.app.state.poller.poke()
    return result


@router.post("/mode")
async def post_mode(request: Request, body: ModeRequest):
    try:
        result = await request.app.state.vpnctl.set_mode(body.mode)
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc
    request.app.state.poller.poke()
    return result


@router.post("/select")
async def post_select(request: Request, body: SelectRequest):
    try:
        result = await request.app.state.vpnctl.select(body.id)
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc
    request.app.state.poller.poke()
    return result


@router.post("/vpn/enable")
async def post_enable(request: Request):
    try:
        result = await request.app.state.vpnctl.enable()
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc
    request.app.state.poller.poke()
    return result


@router.post("/vpn/disable")
async def post_disable(request: Request):
    try:
        result = await request.app.state.vpnctl.disable()
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc
    request.app.state.poller.poke()
    return result
