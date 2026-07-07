"""E2E harness: boot the real backend (and proxy) as subprocesses and drive
them over HTTP/SSE, as a browser or an adapter would.

These differ from the in-process (ASGITransport) suites on purpose — they
exercise what only a real server can: long-lived SSE over the wire, a real
uvicorn lifespan (boot-time graph rehydration), cross-service proxy → backend
uploads, outbound webhook delivery, and process restart (durability). Slower, so
they carry the `e2e` marker; deselect with `-m 'not e2e'`.
"""
from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.e2e


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_http(url: str, proc: subprocess.Popen[bytes], logs: Path, timeout: float = 40.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"process exited early ({proc.returncode}):\n{logs.read_text()}")
        try:
            httpx.get(url, timeout=1.0)
            return
        except Exception:  # noqa: BLE001 - not up yet
            time.sleep(0.2)
    raise TimeoutError(f"{url} not ready in {timeout}s:\n{logs.read_text()}")


class Backend:
    """A real uvicorn-hosted backend on an ephemeral port. Config flows through
    the env the `main:app` factory reads, so auth / signing / DB are all
    controllable. Restartable on the same DB (for durability tests)."""

    def __init__(self, db_path: Path, log_path: Path, env_extra: dict[str, str] | None = None) -> None:
        self.db_path = db_path
        self.log_path = log_path
        self.env_extra = env_extra or {}
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self._proc: subprocess.Popen[bytes] | None = None

    def start(self) -> "Backend":
        env = os.environ.copy()
        env.update(
            {
                "AXOR_DATABASE_URL": f"sqlite+aiosqlite:///{self.db_path}",
                "PYTHONUNBUFFERED": "1",
                **self.env_extra,
            }
        )
        log = self.log_path.open("wb")
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "axor_backend.main:app",
             "--factory", "--host", "127.0.0.1", "--port", str(self.port)],
            env=env, stdout=log, stderr=subprocess.STDOUT,
        )
        _wait_http(f"{self.url}/v1/auth/status", self._proc, self.log_path)
        return self

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                self._proc.wait(5)
            if self._proc.poll() is None:
                self._proc.kill()
        self._proc = None


class Proxy:
    """The observe-only proxy in demo-mode, uploading to a backend."""

    def __init__(self, backend_url: str, trace_dir: Path, log_path: Path) -> None:
        self.backend_url = backend_url
        self.trace_dir = trace_dir
        self.log_path = log_path
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self._proc: subprocess.Popen[bytes] | None = None

    def start(self) -> "Proxy":
        env = os.environ.copy()
        env.update(
            {
                "AXOR_PROXY_DEMO": "1",
                "AXOR_PROXY_PORT": str(self.port),
                "AXOR_PROXY_HOST": "127.0.0.1",
                "AXOR_BACKEND_URL": self.backend_url,
                "AXOR_TRACE_DIR": str(self.trace_dir),
                "PYTHONUNBUFFERED": "1",
            }
        )
        log = self.log_path.open("wb")
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "axor_proxy.main"],
            env=env, stdout=log, stderr=subprocess.STDOUT,
        )
        _wait_http(f"{self.url}/axor/healthz", self._proc, self.log_path)
        return self

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                self._proc.wait(5)
            if self._proc.poll() is None:
                self._proc.kill()


class WebhookSink:
    """A throwaway HTTP endpoint that records every POST it receives — stands in
    for a Slack/PagerDuty webhook so notification delivery can be asserted."""

    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []
        port = _free_port()
        self.url = f"http://127.0.0.1:{port}/hook"
        sink = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("content-length", 0))
                body = self.rfile.read(length)
                with contextlib.suppress(Exception):
                    sink.received.append(json.loads(body))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *_args: Any) -> None:  # silence
                return

        self._server = HTTPServer(("127.0.0.1", port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self) -> "WebhookSink":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()

    def wait_for(self, predicate: Any, timeout: float = 10.0) -> dict[str, Any]:  # noqa: ANN401
        deadline = time.time() + timeout
        while time.time() < deadline:
            for msg in list(self.received):
                if predicate(msg):
                    return msg
            time.sleep(0.1)
        raise TimeoutError(f"no matching webhook within {timeout}s; got {self.received}")


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def backend(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Backend]:
    d = tmp_path_factory.mktemp("backend")
    # Open posture (no auth, unsigned commands) — the shared fixture for the
    # journeys that don't test auth/signing; those boot their own instance.
    b = Backend(d / "axor.db", d / "backend.log", {"AXOR_ALLOW_UNSIGNED": "1"}).start()
    yield b
    b.stop()


@pytest.fixture(scope="session")
def proxy(backend: Backend, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Proxy]:
    d = tmp_path_factory.mktemp("proxy")
    p = Proxy(backend.url, d / "traces", d / "proxy.log").start()
    yield p
    p.stop()


@pytest.fixture
async def http(backend: Backend) -> Iterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(base_url=backend.url, timeout=15.0) as c:
        yield c


@pytest.fixture
async def proxy_http(proxy: Proxy) -> Iterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(base_url=proxy.url, timeout=30.0) as c:
        yield c


@pytest.fixture
def webhook() -> Iterator[WebhookSink]:
    s = WebhookSink().start()
    yield s
    s.stop()


async def read_sse(
    client: httpx.AsyncClient, path: str, want_event: str, timeout: float = 10.0,
) -> dict[str, Any]:
    """Read the real text/event-stream until an `event:` of `want_event` with a
    JSON `data:` arrives; return the parsed data. Used for the audit and desired
    streams (which a browser reads with EventSource)."""
    async with client.stream("GET", path, timeout=timeout) as resp:
        assert resp.status_code == 200, f"stream {path} -> {resp.status_code}"
        event: str | None = None
        async for line in resp.aiter_lines():
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:") and event == want_event:
                return json.loads(line[len("data:"):].strip())
    raise AssertionError(f"stream {path} ended before a {want_event!r} event")
