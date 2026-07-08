"""stdio-MCP gateway — front a *local* MCP server (the `command:` kind every
desktop client config is full of) with the same proxied-tool surface as an
HTTP endpoint.

Most MCP servers in the wild are stdio processes (`npx …`, `uvx …`), not HTTP
services. Without a gateway they are invisible to the proxy — and "point us at
your MCP server" fails at first contact. This module closes that gap: the
proxy spawns the process, speaks JSON-RPC over newline-delimited stdio
(MCP stdio transport), and exposes it at /t/{name}/ like any other tool —
fault injection and observation included.

Trust posture: the process runs *locally, next to the proxy*, spawned from an
operator-supplied command. That is config, not runtime input — the same trust
class as the tool table itself. Auth passthrough is moot here (no wire to
carry headers on); observation still records sizes/hashes only, never raw
bodies.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from axor_proxy.mcp import PROTOCOL_VERSION, McpError

# A stderr tail is kept for diagnostics (MCP servers log there); bounded so a
# chatty server cannot grow memory.
_STDERR_TAIL = 4096


class StdioMcpServer:
    """One spawned stdio MCP server: subprocess + serialized JSON-RPC calls.

    MCP stdio framing is newline-delimited JSON. Calls are serialized under a
    lock — stdio has no multiplexing worth fighting for at proxy volumes — and
    server-initiated notifications interleaved with a response are skipped.
    """

    def __init__(self, command: list[str], rpc_timeout: float = 15.0) -> None:
        self.command = command
        self.rpc_timeout = rpc_timeout
        self.server_name = "mcp-server"
        self.protocol_version = PROTOCOL_VERSION
        self.tools: list[dict[str, str]] = []
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._next_id = 0
        self._stderr_tail = b""
        self._stderr_task: asyncio.Task | None = None

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> None:
        """Spawn the process and run the MCP handshake + tools/list."""
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (OSError, ValueError) as exc:
            raise McpError(f"cannot spawn {self.command[0]!r}: {exc}") from exc
        # Drain stderr continuously — an unread pipe fills and deadlocks the
        # server. Keep only a bounded tail for error messages.
        self._stderr_task = asyncio.get_running_loop().create_task(
            self._drain_stderr()
        )

        init = await self.rpc({
            "jsonrpc": "2.0", "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "axor-proxy", "version": "0.1"},
            },
        })
        result = (init or {}).get("result")
        if not isinstance(result, dict):
            raise McpError(f"initialize: malformed result{self._stderr_hint()}")
        info = result.get("serverInfo", {})
        self.server_name = info.get("name", "mcp-server")
        self.protocol_version = result.get("protocolVersion", PROTOCOL_VERSION)

        await self.rpc({"jsonrpc": "2.0", "method": "notifications/initialized"})

        listed = await self.rpc({"jsonrpc": "2.0", "method": "tools/list", "params": {}})
        tools = ((listed or {}).get("result") or {}).get("tools")
        if not isinstance(tools, list):
            raise McpError(f"tools/list: malformed result{self._stderr_hint()}")
        self.tools = [
            {"name": t.get("name", ""), "description": t.get("description", "")}
            for t in tools if isinstance(t, dict)
        ]

    async def rpc(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Send one JSON-RPC message. Requests get an id assigned here and the
        matching response is returned; notifications (no id in MCP semantics —
        method starts with 'notifications/') return None."""
        if self._proc is None or not self.alive:
            raise McpError(f"server process not running{self._stderr_hint()}")
        is_notification = str(payload.get("method", "")).startswith("notifications/")
        msg = dict(payload)
        if not is_notification:
            self._next_id += 1
            msg["id"] = self._next_id
        line = json.dumps(msg, separators=(",", ":")).encode() + b"\n"

        async with self._lock:
            assert self._proc.stdin is not None and self._proc.stdout is not None
            try:
                self._proc.stdin.write(line)
                await self._proc.stdin.drain()
                if is_notification:
                    return None
                return await asyncio.wait_for(
                    self._read_response(msg["id"]), timeout=self.rpc_timeout
                )
            except TimeoutError as exc:
                raise McpError(
                    f"rpc timeout after {self.rpc_timeout}s{self._stderr_hint()}"
                ) from exc
            except (ConnectionError, BrokenPipeError, OSError) as exc:
                raise McpError(f"server pipe broke: {exc}{self._stderr_hint()}") from exc

    async def _read_response(self, want_id: int) -> dict[str, Any]:
        """Read lines until the response with our id; skip interleaved
        notifications/logs. EOF means the process died."""
        assert self._proc is not None and self._proc.stdout is not None
        while True:
            raw = await self._proc.stdout.readline()
            if not raw:
                raise McpError(f"server closed stdout{self._stderr_hint()}")
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                continue  # not JSON-RPC (stray print) — skip, stderr is for logs
            if isinstance(parsed, dict) and parsed.get("id") == want_id:
                return parsed

    async def _drain_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        while True:
            chunk = await self._proc.stderr.read(1024)
            if not chunk:
                return
            self._stderr_tail = (self._stderr_tail + chunk)[-_STDERR_TAIL:]

    def _stderr_hint(self) -> str:
        tail = self._stderr_tail.decode(errors="replace").strip()
        return f" · stderr: {tail[-300:]}" if tail else ""

    async def close(self) -> None:
        if self._stderr_task is not None:
            self._stderr_task.cancel()
        if self._proc is None or self._proc.returncode is not None:
            return
        self._proc.terminate()
        try:
            await asyncio.wait_for(self._proc.wait(), timeout=3.0)
        except TimeoutError:
            self._proc.kill()
            await self._proc.wait()


async def discover_stdio(command: list[str]) -> StdioMcpServer:
    """Spawn + handshake a stdio MCP server; returns the live server handle
    (caller owns it — register it or close it). Raises McpError on anything
    that is not a healthy MCP server, with the process cleaned up."""
    server = StdioMcpServer(command)
    try:
        await server.start()
    except McpError:
        await server.close()
        raise
    return server
