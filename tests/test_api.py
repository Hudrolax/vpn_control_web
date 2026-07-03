import json
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.cache import StateCache
from app.main import app
from app.models import ServerList, VpnStatus
from app.poller import Poller


class FakeDB:
    async def history(self, server_id, hours):
        return []

    async def record_checks(self, servers):
        pass

    async def prune(self):
        pass


@pytest.fixture
async def client():
    status = VpnStatus.model_validate(
        json.loads(
            '{"ok": true, "enabled": true, "mode": "auto-sticky",'
            ' "selected": {"id": "sub-001-x", "name": "X"},'
            ' "subscription": {"server_count": 1}}'
        )
    )
    servers = ServerList.model_validate(
        {"ok": True, "servers": [{"id": "sub-001-x", "name": "X", "selected": True}]}
    )

    app.state.cache = StateCache()
    await app.state.cache.set(status, servers.servers)
    app.state.db = FakeDB()
    app.state.vpnctl = AsyncMock()
    app.state.vpnctl.select.return_value = {"ok": True}
    app.state.poller = AsyncMock(spec=Poller)
    app.state.poller.poke = lambda: None

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_healthz(client):
    r = await client.get("/healthz")
    assert r.status_code == 200


async def test_status_from_cache(client):
    r = await client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert body["status"]["mode"] == "auto-sticky"
    assert body["stale"] is False


async def test_servers_from_cache(client):
    r = await client.get("/api/servers")
    assert r.status_code == 200
    assert r.json()["servers"][0]["id"] == "sub-001-x"


async def test_select_validates_id(client):
    r = await client.post("/api/select", json={"id": "not-a-valid-id; rm -rf /"})
    assert r.status_code == 422

    r = await client.post("/api/select", json={"id": "sub-001-x"})
    assert r.status_code == 200


async def test_subscription_url_validates(client):
    app.state.vpnctl.set_subscription_url.return_value = {"ok": True}

    r = await client.post("/api/subscription/url", json={"url": "not a url"})
    assert r.status_code == 422

    r = await client.post("/api/subscription/url", json={"url": "https://ex.com/sub?a=1 ; id"})
    assert r.status_code == 422

    r = await client.post("/api/subscription/url", json={"url": "https://ex.com/sub?token=abc"})
    assert r.status_code == 200
    app.state.vpnctl.set_subscription_url.assert_awaited_with("https://ex.com/sub?token=abc")


async def test_subscription_interval_validates(client):
    app.state.vpnctl.set_refresh_interval.return_value = {"ok": True}

    r = await client.post("/api/subscription/interval", json={"interval_sec": 1234})
    assert r.status_code == 422

    r = await client.post("/api/subscription/interval", json={"interval_sec": 14400})
    assert r.status_code == 200
    app.state.vpnctl.set_refresh_interval.assert_awaited_with(14400)


async def test_dashboard_renders(client):
    r = await client.get("/")
    assert r.status_code == 200
    assert "VPN Control" in r.text
