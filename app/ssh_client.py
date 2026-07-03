from __future__ import annotations

import asyncio
import logging
import os
import shlex
from dataclasses import dataclass

import asyncssh

log = logging.getLogger(__name__)


class SSHError(Exception):
    """SSH transport or auth failure."""


class CommandTimeout(Exception):
    """Remote command exceeded configured timeout."""


@dataclass
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str


class OpenWrtSSH:
    """
    Async SSH executor for the OpenWrt router. Creates a fresh connection per
    request — command cadence is very low, so the simpler model is fine.

    The router side enforces a forced command (vpnctl-ssh) which validates the
    whole command line against a whitelist regex, so only `vpnctl <subcmd>`
    strings ever reach the shell there.
    """

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        key_path: str,
        known_hosts_path: str,
        connect_timeout_sec: float,
        command_timeout_sec: float,
    ):
        self._host = host
        self._port = port
        self._username = username
        self._key_path = key_path
        self._known_hosts_path = known_hosts_path
        self._connect_timeout = connect_timeout_sec
        self._command_timeout = command_timeout_sec

    async def run(self, argv: list[str], timeout_sec: float | None = None) -> CommandResult:
        """
        Run a command on the router. `argv` is a list of tokens; each token is
        shell-quoted with shlex before being joined into the final ssh command.
        Never accepts a raw shell string — this keeps HTTP input out of the
        router shell interpreter.
        """
        if not argv:
            raise ValueError("argv must not be empty")
        command = " ".join(shlex.quote(a) for a in argv)
        cmd_timeout = timeout_sec if timeout_sec is not None else self._command_timeout
        log.info("ssh exec", extra={"host": self._host, "cmd": command})

        known_hosts: str | None = self._known_hosts_path
        if not known_hosts or not os.path.exists(known_hosts):
            # Bootstrap path: allow TOFU on first run. Once the admin commits
            # known_hosts, strict verification kicks in automatically.
            known_hosts = None

        try:
            conn_ctx = asyncssh.connect(
                host=self._host,
                port=self._port,
                username=self._username,
                client_keys=[self._key_path],
                known_hosts=known_hosts,
                connect_timeout=self._connect_timeout,
            )
            async with await asyncio.wait_for(conn_ctx, timeout=self._connect_timeout) as conn:
                try:
                    proc = await asyncio.wait_for(
                        conn.run(command, check=False),
                        timeout=cmd_timeout,
                    )
                except asyncio.TimeoutError as exc:
                    raise CommandTimeout(
                        f"command timed out after {cmd_timeout}s: {command}"
                    ) from exc
        except (asyncssh.Error, OSError, asyncio.TimeoutError) as exc:
            raise SSHError(f"ssh to {self._host} failed: {exc}") from exc

        stdout = proc.stdout if isinstance(proc.stdout, str) else proc.stdout.decode(errors="replace")
        stderr = proc.stderr if isinstance(proc.stderr, str) else proc.stderr.decode(errors="replace")
        exit_code = int(proc.exit_status if proc.exit_status is not None else -1)
        log.info(
            "ssh exec done",
            extra={"exit_code": exit_code, "stdout_len": len(stdout), "stderr_len": len(stderr)},
        )
        return CommandResult(exit_code=exit_code, stdout=stdout, stderr=stderr)
