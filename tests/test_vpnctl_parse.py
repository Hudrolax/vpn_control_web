import pytest

from app.models import ServerList, VpnStatus
from app.ssh_client import CommandResult
from app.vpnctl import VpnctlBusy, VpnctlClient, VpnctlError

STATUS_JSON = """
{"ok": true, "enabled": true, "mode": "auto-sticky",
 "selected": {"id": "sub-006-2.26.128.73", "name": "Germany", "address": "2.26.128.73"},
 "selected_health": {"status": "ok", "latency_ms": 178, "last_checked": 1782820300},
 "subscription": {"last_refresh": 1782820008, "server_count": 9, "last_error": null},
 "services": {"xray": "running", "singbox": "running", "pbr": "enabled"},
 "busy": null, "warning": null, "version": 1}
"""

LIST_JSON = """
{"ok": true, "servers": [
  {"id": "sub-006-2.26.128.73", "name": "Germany", "address": "2.26.128.73", "port": 443,
   "status": "ok", "latency_ms": 178, "last_error": null, "last_checked": 1782820300,
   "selected": true},
  {"id": "sub-009-46.243.142.32", "name": "sub-009", "address": "46.243.142.32", "port": 443,
   "status": "down", "latency_ms": null, "last_error": "timeout", "last_checked": 1782820300,
   "selected": false}
]}
"""


def _result(stdout: str, exit_code: int = 0) -> CommandResult:
    return CommandResult(exit_code=exit_code, stdout=stdout, stderr="")


def test_parse_status():
    data = VpnctlClient._parse(_result(STATUS_JSON), "status")
    status = VpnStatus.model_validate(data)
    assert status.enabled is True
    assert status.mode == "auto-sticky"
    assert status.selected.id == "sub-006-2.26.128.73"
    assert status.selected_health.latency_ms == 178
    assert status.subscription.server_count == 9


def test_parse_list():
    data = VpnctlClient._parse(_result(LIST_JSON), "list")
    servers = ServerList.model_validate(data)
    assert len(servers.servers) == 2
    assert servers.servers[0].selected is True
    assert servers.servers[1].status == "down"
    assert servers.servers[1].latency_ms is None


def test_parse_busy_error():
    payload = '{"ok": false, "error": "busy", "message": "locked"}'
    with pytest.raises(VpnctlBusy):
        VpnctlClient._parse(_result(payload, exit_code=1), "select")


def test_parse_domain_error():
    payload = '{"ok": false, "error": "unknown_server", "message": "no such server"}'
    with pytest.raises(VpnctlError) as exc:
        VpnctlClient._parse(_result(payload, exit_code=1), "select")
    assert exc.value.code == "unknown_server"


def test_parse_non_json():
    with pytest.raises(VpnctlError) as exc:
        VpnctlClient._parse(_result("Connection closed"), "status")
    assert exc.value.code == "bad_json"


def test_parse_empty():
    with pytest.raises(VpnctlError) as exc:
        VpnctlClient._parse(_result(""), "status")
    assert exc.value.code == "empty_response"
