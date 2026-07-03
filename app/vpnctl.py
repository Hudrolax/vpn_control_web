from __future__ import annotations

import json
import logging
from typing import Any

from .models import LogsResponse, ServerList, VpnStatus
from .ssh_client import CommandResult, OpenWrtSSH

log = logging.getLogger(__name__)


class VpnctlError(Exception):
    def __init__(self, code: str, message: str = ""):
        self.code = code
        self.message = message or code
        super().__init__(self.message)


class VpnctlBusy(VpnctlError):
    def __init__(self, message: str = "another operation is in progress"):
        super().__init__("busy", message)


class VpnctlClient:
    """Typed client for the vpnctl JSON API on the OpenWrt router."""

    def __init__(self, ssh: OpenWrtSSH):
        self._ssh = ssh

    async def _call(self, *args: str, timeout_sec: float | None = None) -> dict[str, Any]:
        result: CommandResult = await self._ssh.run(["vpnctl", *args], timeout_sec=timeout_sec)
        return self._parse(result, " ".join(args))

    @staticmethod
    def _parse(result: CommandResult, cmd: str) -> dict[str, Any]:
        text = result.stdout.strip()
        if not text:
            stderr = result.stderr.strip()[:200]
            raise VpnctlError("empty_response", f"vpnctl {cmd}: empty stdout, stderr={stderr}")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise VpnctlError("bad_json", f"vpnctl {cmd}: non-JSON output: {text[:200]}") from exc
        if not isinstance(data, dict):
            got = type(data).__name__
            raise VpnctlError("bad_json", f"vpnctl {cmd}: expected object, got {got}")
        if not data.get("ok", False):
            code = str(data.get("error", "unknown"))
            message = str(data.get("message", ""))
            if code == "busy":
                raise VpnctlBusy(message or "busy")
            raise VpnctlError(code, message or f"vpnctl {cmd} failed: {code}")
        return data

    async def status(self) -> VpnStatus:
        return VpnStatus.model_validate(await self._call("status"))

    async def list_servers(self) -> ServerList:
        return ServerList.model_validate(await self._call("list"))

    async def logs(self, limit: int = 50) -> LogsResponse:
        return LogsResponse.model_validate(await self._call("logs", str(limit)))

    async def refresh(self, force: bool = False) -> dict[str, Any]:
        args = ["refresh", "--force"] if force else ["refresh"]
        return await self._call(*args, timeout_sec=120)

    async def check(self, server_id: str | None = None) -> dict[str, Any]:
        args = ["check"] if server_id is None else ["check", server_id]
        return await self._call(*args)

    async def set_mode(self, mode: str) -> dict[str, Any]:
        return await self._call("mode", mode)

    async def select(self, server_id: str) -> dict[str, Any]:
        return await self._call("select", server_id, timeout_sec=60)

    async def enable(self) -> dict[str, Any]:
        return await self._call("enable", timeout_sec=60)

    async def disable(self) -> dict[str, Any]:
        return await self._call("disable", timeout_sec=60)
