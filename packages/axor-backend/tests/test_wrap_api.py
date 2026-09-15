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


@needs_wrap
async def test_scan_refuses_two_files_under_one_path(client: httpx.AsyncClient) -> None:
    """The browser's plain file picker exposes no directory, so it sends bare
    filenames — and two modules both called tools.py used to overwrite each
    other in the temp tree. The scan then answered 200 with the tools of
    whichever won, and the Config Builder said "Found 1 tools" for an agent
    with two: the missing one is undeclared, and undeclared is denied."""
    resp = await client.post("/v1/wrap/scan", json={"files": [
        {"path": "tools.py", "content": AGENT_PY},
        {"path": "tools.py", "content": "# a different module, same name\n"},
    ]})
    assert resp.status_code == 400
    assert "share the path 'tools.py'" in resp.json()["detail"]


@needs_wrap
async def test_scan_reports_the_files_it_could_not_parse(
    client: httpx.AsyncClient,
) -> None:
    """`scan_project` skips what it cannot parse — honest inside the engine,
    which can only return tools. Dropped from the response it became silent:
    a stray syntax error, a newer grammar, a bad encoding, and the file's
    tools are simply not in the answer with nothing pointing at it."""
    resp = await client.post("/v1/wrap/scan", json={"files": [
        {"path": "pkg/tools.py", "content": AGENT_PY},
        {"path": "pkg/broken.py", "content": "def broken(:\n"},
        {"path": "pkg/binary.py", "content": "x = 1\x00\n"},
    ]})
    assert resp.status_code == 200
    body = resp.json()
    assert {t["id"] for t in body["tools"]} == {"read_inbox", "send_email"}
    skipped = {f["path"]: f["reason"] for f in body["skipped"]}
    assert set(skipped) == {"pkg/broken.py", "pkg/binary.py"}
    assert "SyntaxError" in skipped["pkg/broken.py"]
    assert "null bytes" in skipped["pkg/binary.py"]


@needs_wrap
async def test_a_clean_scan_reports_nothing_skipped(client: httpx.AsyncClient) -> None:
    resp = await client.post("/v1/wrap/scan", json=_scan_body())
    assert resp.status_code == 200 and resp.json()["skipped"] == []


@needs_wrap
async def test_scan_bounds_the_file_count_not_only_the_bytes(
    client: httpx.AsyncClient,
) -> None:
    """A zero-byte file adds nothing to the byte total, so MAX_SCAN_BYTES did
    not bound the count at all — 50 000 empty files answered 200 after 18s of
    mkdir/write/rglob on one request."""
    from axor_backend.wrap_api import MAX_SCAN_FILES

    files = [{"path": f"d{i}/x.py", "content": ""} for i in range(MAX_SCAN_FILES + 1)]
    resp = await client.post("/v1/wrap/scan", json={"files": files})
    assert resp.status_code == 413
    assert "2000 files" in resp.json()["detail"]

    at_limit = files[:MAX_SCAN_FILES]
    assert (await client.post("/v1/wrap/scan",
                              json={"files": at_limit})).status_code == 200


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


@needs_wrap
async def test_an_effect_list_given_as_a_string_is_refused(
    client: httpx.AsyncClient,
) -> None:
    """`_str_list` used to answer [] for anything that was not a list.

    `driving_args: "to"` then compiled to a manifest that validates, a 200, and
    a governance.yaml with no driving_args and no untrusted_sources — the
    artifact the operator ships, gating nothing. The same typo in
    sensitive_fields drops sensitive_sources, so the confidentiality floor
    never arms.
    """
    tools = {t["id"]: t for t in await _scanned_tools(client)}
    base = _classified(tools["send_email"], "EXPORT")
    for field, value in (("driving_args", "to"),
                         ("untrusted_fields", "result.*"),
                         ("sensitive_fields", "result.body"),
                         ("driving_args", {"to": True}),
                         ("driving_args", [1])):
        tool = {**base, "effect": {**base["effect"], field: value}}
        resp = await client.post("/v1/wrap/manifests", json={"tools": [tool]})
        assert resp.status_code == 400, (field, value)
        assert f"effect.{field}" in resp.json()["detail"]

    # Absent stays absent — the fields are optional, only wrong is refused.
    ok = {**base, "effect": {"default_class": "EXPORT"}}
    assert (await client.post("/v1/wrap/manifests",
                              json={"tools": [ok]})).status_code == 200


@needs_wrap
async def test_the_compiled_governance_carries_what_was_declared(
    client: httpx.AsyncClient,
) -> None:
    """The other half of the test above: correctly declared lists must reach
    the YAML, so "refused" is not passing by way of compiling nothing."""
    tools = {t["id"]: t for t in await _scanned_tools(client)}
    tool = _classified(tools["send_email"], "EXPORT")
    tool["effect"]["driving_args"] = ["to", "body"]
    tool["effect"]["sensitive_fields"] = ["result.body"]
    out = (await client.post("/v1/wrap/manifests", json={"tools": [tool]})).json()
    yaml = out["governance_yaml"]
    assert "driving_args:" in yaml and '- "to"' in yaml and '- "body"' in yaml
    assert "sensitive_sources:" in yaml


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
