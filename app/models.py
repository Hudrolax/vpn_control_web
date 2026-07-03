from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SERVER_ID_PATTERN = r"^sub-[A-Za-z0-9._-]{1,64}$"

Mode = Literal["auto-sticky", "auto-best", "manual"]

# Refresh periods selectable in the UI: 1h, 4h, 6h, 12h, 24h.
RefreshInterval = Literal[3600, 14400, 21600, 43200, 86400]

SUBSCRIPTION_URL_PATTERN = r"^https?://[A-Za-z0-9._~:/?#@!$&()*+,;=%-]+$"


class SelectedServer(BaseModel):
    id: str
    name: str | None = None
    address: str | None = None


class SelectedHealth(BaseModel):
    status: str = "unchecked"
    latency_ms: int | None = None
    last_checked: int | None = None


class SubscriptionInfo(BaseModel):
    last_refresh: int | None = None
    server_count: int = 0
    last_error: str | None = None
    refresh_interval_sec: int | None = None
    url_host: str | None = None


class ServicesInfo(BaseModel):
    xray: str = "unknown"
    singbox: str = "unknown"
    pbr: str = "unknown"


class VpnStatus(BaseModel):
    ok: bool
    enabled: bool = True
    mode: Mode = "auto-sticky"
    selected: SelectedServer | None = None
    selected_health: SelectedHealth | None = None
    subscription: SubscriptionInfo = SubscriptionInfo()
    services: ServicesInfo = ServicesInfo()
    busy: str | None = None
    warning: str | None = None
    version: int = 1


class Server(BaseModel):
    id: str
    name: str | None = None
    address: str | None = None
    port: int | None = None
    status: str = "unchecked"
    latency_ms: int | None = None
    last_error: str | None = None
    last_checked: int | None = None
    selected: bool = False


class ServerList(BaseModel):
    ok: bool
    servers: list[Server] = []


class LogEntry(BaseModel):
    ts: int
    actor: str = "unknown"
    action: str
    detail: str | None = None


class LogsResponse(BaseModel):
    ok: bool
    entries: list[LogEntry] = []


class ModeRequest(BaseModel):
    mode: Mode


class SelectRequest(BaseModel):
    id: str = Field(pattern=SERVER_ID_PATTERN)


class SubscriptionUrlRequest(BaseModel):
    url: str = Field(pattern=SUBSCRIPTION_URL_PATTERN, max_length=1024)


class RefreshIntervalRequest(BaseModel):
    interval_sec: RefreshInterval


class HistoryPoint(BaseModel):
    ts: int
    server_id: str
    status: str
    latency_ms: int | None = None
