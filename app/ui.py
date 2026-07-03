from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .models import SERVER_ID_PATTERN, ModeRequest, SelectRequest
from .vpnctl import VpnctlBusy, VpnctlError

router = APIRouter()

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["ago"] = lambda ts: _ago(ts)


def _ago(ts: int | float | None) -> str:
    if not ts:
        return "—"
    delta = int(time.time() - float(ts))
    if delta < 0:
        delta = 0
    if delta < 60:
        return f"{delta}s ago"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h {delta % 3600 // 60}m ago"
    return f"{delta // 86400}d ago"


async def _render_status(request: Request, message: str | None = None) -> HTMLResponse:
    snapshot = await request.app.state.cache.get()
    return templates.TemplateResponse(
        request, "_status.html", {"snap": snapshot, "message": message}
    )


def _sparkline(points: list, width: int = 72, height: int = 18) -> str | None:
    """Inline SVG polyline for the last day of latency values of one server."""
    values = [p.latency_ms for p in points if p.latency_ms is not None]
    if len(values) < 2:
        return None
    values = values[-40:]
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    step = width / (len(values) - 1)
    coords = " ".join(
        f"{i * step:.1f},{height - 2 - (v - lo) / span * (height - 4):.1f}"
        for i, v in enumerate(values)
    )
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        f'<polyline points="{coords}" fill="none" stroke="#38bdf8" stroke-width="1.5"/></svg>'
    )


async def _render_servers(request: Request, message: str | None = None) -> HTMLResponse:
    snapshot = await request.app.state.cache.get()
    sparks: dict[str, str] = {}
    try:
        points = await request.app.state.db.history(None, hours=24)
        by_server: dict[str, list] = {}
        for p in points:
            by_server.setdefault(p.server_id, []).append(p)
        for sid, pts in by_server.items():
            svg = _sparkline(pts)
            if svg:
                sparks[sid] = svg
    except Exception:  # noqa: BLE001 - sparkline is decorative
        pass
    return templates.TemplateResponse(
        request, "_servers.html", {"snap": snapshot, "message": message, "sparks": sparks}
    )


async def _mutate(request: Request, action) -> str | None:
    """Run a vpnctl mutation, return a user-visible error message or None."""
    try:
        await action()
    except VpnctlBusy:
        return "Роутер занят другой операцией, попробуйте позже"
    except VpnctlError as exc:
        return f"Ошибка vpnctl: {exc.code}: {exc.message}"
    except Exception as exc:  # noqa: BLE001
        return f"Ошибка связи с роутером: {exc}"
    request.app.state.poller.poke()
    return None


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    snapshot = await request.app.state.cache.get()
    return templates.TemplateResponse(request, "dashboard.html", {"snap": snapshot})


@router.get("/partials/status", response_class=HTMLResponse)
async def partial_status(request: Request):
    return await _render_status(request)


@router.get("/partials/servers", response_class=HTMLResponse)
async def partial_servers(request: Request):
    return await _render_servers(request)


@router.get("/partials/logs", response_class=HTMLResponse)
async def partial_logs(request: Request):
    entries: list = []
    error: str | None = None
    try:
        entries = (await request.app.state.vpnctl.logs(50)).entries
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
    return templates.TemplateResponse(
        request, "_logs.html", {"entries": entries, "error": error}
    )


@router.post("/actions/refresh", response_class=HTMLResponse)
async def action_refresh(request: Request):
    message = await _mutate(request, lambda: request.app.state.vpnctl.refresh())
    return await _render_status(request, message)


@router.post("/actions/check", response_class=HTMLResponse)
async def action_check(request: Request):
    message = await _mutate(request, lambda: request.app.state.vpnctl.check())
    return await _render_servers(request, message)


@router.post("/actions/mode", response_class=HTMLResponse)
async def action_mode(request: Request, mode: str = Form(...)):
    body = ModeRequest(mode=mode)  # validates value
    message = await _mutate(request, lambda: request.app.state.vpnctl.set_mode(body.mode))
    return await _render_status(request, message)


@router.post("/actions/select", response_class=HTMLResponse)
async def action_select(request: Request, server_id: str = Form(..., pattern=SERVER_ID_PATTERN)):
    body = SelectRequest(id=server_id)  # validates format
    message = await _mutate(request, lambda: request.app.state.vpnctl.select(body.id))
    return await _render_servers(request, message)


@router.post("/actions/enable", response_class=HTMLResponse)
async def action_enable(request: Request):
    message = await _mutate(request, lambda: request.app.state.vpnctl.enable())
    return await _render_status(request, message)


@router.post("/actions/disable", response_class=HTMLResponse)
async def action_disable(request: Request):
    message = await _mutate(request, lambda: request.app.state.vpnctl.disable())
    return await _render_status(request, message)
