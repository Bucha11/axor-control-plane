"""Wrap engine API (/v1/wrap): real code scan + manifest compilation.

The engine (axor-wrap) is an optional extra: tests that exercise the scan
skip when it is absent; the 501 contract is tested by masking the import.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

import httpx
import pytest
from axor_backend.app import create_app

needs_wrap = pytest.mark.skipif(
    importlib.util.find_spec("axor_wrap") is None,
    reason="axor-wrap not installed (axor-backend[wrap] extra)",
)

# A small langchain agent: one read tool (untrusted ingest) + one send tool.
AGENT_PY = '''\
from langchain_core.tools import tool


@tool
def read_inbox(folder: str) -> str:
    """Read the user's email inbox."""
    return folder


@tool
def send_email(to: str, body: str) -> str:
    """Send an email to a recipient."""
    return to
'''


@pytest.fixture
async def client(tmp_path: pathlib.Path) -> httpx.AsyncClient:
    app = create_app(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/wrap.db",
        operator_keys={}, allow_unsigned=True,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://backend.test"
    ) as c:
        async with app.router.lifespan_context(app):
            yield c


def _scan_body(path: str = "my_agent/tools.py", content: str = AGENT_PY) -> dict:
    return {"files": [{"path": path, "content": content}]}


# ── scan ──────────────────────────────────────────────────────────────────────

@needs_wrap
async def test_scan_detects_tools_and_guesses(client: httpx.AsyncClient) -> None:
    resp = await client.post("/v1/wrap/scan", json=_scan_body())
    assert resp.status_code == 200
    tools = {t["id"]: t for t in resp.json()["tools"]}
    assert set(tools) == {"read_inbox", "send_email"}

    read = tools["read_inbox"]
    assert read["framework"] == "langchain"
    assert read["source"].startswith("my_agent/tools.py:")
    assert read["schema_confidence"] == "high"
    assert read["args_schema"]["properties"]["folder"] == {"type": "string"}
    assert read["guess"]["default_class"] == "READ"
    assert read["guess"]["confidence"] == "high"
    assert "READ" in read["guess"]["reason"]
    # an inbox read ingests external content → coarse untrusted candidate
    assert read["guess"]["untrusted_fields"] == ["result.*"]

    send = tools["send_email"]
    assert send["guess"]["default_class"] == "EXPORT"
    assert "to" in send["guess"]["driving_args"]
    assert send["description"] == "Send an email to a recipient."


@needs_wrap
async def test_scan_rejects_traversal_and_absolute_paths(client: httpx.AsyncClient) -> None:
    for bad in ("../evil.py", "/etc/evil.py", "a/../../evil.py", "c:\\evil.py", "~/evil.py"):
        resp = await client.post("/v1/wrap/scan", json=_scan_body(path=bad))
        assert resp.status_code == 400, bad


@needs_wrap
async def test_scan_rejects_non_python_files(client: httpx.AsyncClient) -> None:
    resp = await client.post("/v1/wrap/scan", json=_scan_body(path="tools.txt"))
    assert resp.status_code == 400
    assert "only .py" in resp.json()["detail"]


@needs_wrap
async def test_scan_rejects_oversized_upload(client: httpx.AsyncClient) -> None:
    big = "# padding\n" * (2 * 1024 * 1024 // 10 + 1)
    resp = await client.post("/v1/wrap/scan", json=_scan_body(content=big))
    assert resp.status_code == 413


@needs_wrap
async def test_scan_requires_files(client: httpx.AsyncClient) -> None:
    assert (await client.post("/v1/wrap/scan", json={})).status_code == 400
    assert (await client.post("/v1/wrap/scan", json={"files": []})).status_code == 400


# ── manifests ─────────────────────────────────────────────────────────────────

async def _scanned_tools(client: httpx.AsyncClient) -> list[dict]:
    resp = await client.post("/v1/wrap/scan", json=_scan_body())
    assert resp.status_code == 200
    return resp.json()["tools"]


def _classified(tool: dict, default_class: str) -> dict:
    return {
        **tool,
        "effect": {
            "default_class": default_class,
            "driving_args": tool["guess"]["driving_args"],
            "untrusted_fields": tool["guess"]["untrusted_fields"],
        },
    }


@needs_wrap
async def test_manifests_from_classified_tools(client: httpx.AsyncClient) -> None:
    from axor_wrap import validate_manifest

    tools = {t["id"]: t for t in await _scanned_tools(client)}
    body = {"tools": [
        _classified(tools["read_inbox"], "READ"),
        _classified(tools["send_email"], "EXPORT"),
    ]}
    resp = await client.post("/v1/wrap/manifests", json=body)
    assert resp.status_code == 200
    out = resp.json()

    manifests = {m["id"]: m for m in out["manifests"]}
    assert set(manifests) == {"read_inbox", "send_email"}
    for manifest in manifests.values():
        assert manifest["schema_version"] == "tool-manifest/v1"
        assert validate_manifest(manifest) == []
    assert manifests["read_inbox"]["side_effecting"] is False
    assert manifests["read_inbox"]["untrusted_fields"] == ["result.*"]
    assert manifests["send_email"]["effect"]["default_class"] == "EXPORT"
    assert "to" in manifests["send_email"]["effect"]["driving_args"]

    yaml = out["governance_yaml"]
    assert "egress_sinks:" in yaml and '"send_email"' in yaml
    assert "untrusted_sources:" in yaml and '"read_inbox"' in yaml

    wrap = out["wrap"]
    assert wrap["manifest_schema"] == "tool-manifest/v1"
    assert {t["id"] for t in wrap["tools"]} == {"read_inbox", "send_email"}


@needs_wrap
async def test_manifests_reject_unknown_class(client: httpx.AsyncClient) -> None:
    tools = await _scanned_tools(client)
    body = {"tools": [_classified(tools[0], "UNKNOWN")]}
    resp = await client.post("/v1/wrap/manifests", json=body)
    assert resp.status_code == 400
    assert "classified" in resp.json()["detail"]

    # missing effect entirely is a 400 too, not a silent EXEC fallback
    resp = await client.post("/v1/wrap/manifests", json={"tools": [{"id": "x"}]})
    assert resp.status_code == 400


@needs_wrap
async def test_manifests_carry_sensitive_fields(client: httpx.AsyncClient) -> None:
    tools = {t["id"]: t for t in await _scanned_tools(client)}
    tool = _classified(tools["read_inbox"], "READ")
    tool["effect"]["sensitive_fields"] = ["result.body"]
    resp = await client.post("/v1/wrap/manifests", json={"tools": [tool]})
    assert resp.status_code == 200
    assert resp.json()["manifests"][0]["sensitive_fields"] == ["result.body"]


# ── engine absent → honest 501 (lazy import, masked via sys.modules) ─────────

async def test_wrap_engine_missing_is_501(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # None in sys.modules makes `import axor_wrap` raise ImportError — the
    # documented CPython behavior, no fragile finder/loader games.
    monkeypatch.setitem(sys.modules, "axor_wrap", None)
    for path, body in (
        ("/v1/wrap/scan", _scan_body()),
        ("/v1/wrap/manifests", {"tools": [{"id": "t", "effect": {"default_class": "READ"}}]}),
    ):
        resp = await client.post(path, json=body)
        assert resp.status_code == 501
        assert "not installed" in resp.json()["detail"]


# ── auth: ingest scope is enough (analysis, not an operational command) ──────

@needs_wrap
async def test_wrap_scope_is_ingest(tmp_path: pathlib.Path) -> None:
    token = "master-wrap-test"
    app = create_app(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/wrap-auth.db",
        operator_keys={}, allow_unsigned=True, api_token=token,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://backend.test"
    ) as c:
        async with app.router.lifespan_context(app):
            master = {"Authorization": f"Bearer {token}"}
            minted = await c.post("/v1/keys", headers=master,
                                  json={"scopes": ["ingest"], "label": "wrap"})
            secret = minted.json()["secret"]
            ingest = {"Authorization": f"Bearer {secret}"}
            assert (await c.post("/v1/wrap/scan", json=_scan_body())).status_code == 401
            resp = await c.post("/v1/wrap/scan", headers=ingest, json=_scan_body())
            assert resp.status_code == 200
            # read-only keys may not submit code for analysis
            ro = await c.post("/v1/keys", headers=master,
                              json={"scopes": ["read"], "label": "ro"})
            ro_h = {"Authorization": f"Bearer {ro.json()['secret']}"}
            denied = await c.post("/v1/wrap/scan", headers=ro_h, json=_scan_body())
            assert denied.status_code == 403
