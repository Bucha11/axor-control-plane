"""Sink-side credential injection (ui-spec §14.2), end to end.

The proxy's first rule is that auth is passthrough, byte-for-byte. §14.2 is the
one deliberate reversal of it, and these tests hold both halves at once: a
vault-mode tool has its credential injected and never held by the agent, and
every other tool is still forwarded untouched.

The backend is the real one, in-process — the point of the feature is the chain
(proxy → plane vault → per-node scope), and a faked dispense would test the
half that was never the problem.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from axor_backend.app import create_app as create_backend
from axor_core.kernel.events import EventKind
from axor_proxy.app import ProxyState, create_app
from axor_proxy.vault import CredentialVault
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

NODE = "proxy"          # the run's node_id, and the enrollment scope
ENDPOINT = "http://upstream.test/search"
SECRET = "sk_live_VAULTED"
MASTER = "master-token"
CREDS = {"x-vault-creds-token": "creds-tok"}


def make_upstream(seen: list[dict]) -> Starlette:
    async def handler(request: Request) -> JSONResponse:
        seen.append({
            "auth": request.headers.get("authorization"),
            "api_key": request.headers.get("x-api-key"),
        })
        return JSONResponse({"results": []})

    return Starlette(routes=[Route("/{path:path}", handler,
                                   methods=["GET", "POST"])])


@pytest.fixture
def seen() -> list[dict]:
    return []


@pytest.fixture
async def backend(tmp_path: Path) -> httpx.AsyncClient:
    app = create_backend(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/backend.db",
        operator_keys={}, allow_unsigned=True, api_token=MASTER,
        vault_creds_token="creds-tok", vault_signing_token="signing-tok",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://backend.test"
    ) as c, app.router.lifespan_context(app):
        yield c


async def enroll(backend: httpx.AsyncClient, **over: object) -> None:
    body = {"tool": "web_search", "endpoint": ENDPOINT, "secret": SECRET,
            "scope_nodes": [NODE]}
    body.update(over)
    r = await backend.post("/v1/vault/creds/enroll",
                           headers={"Authorization": f"Bearer {MASTER}", **CREDS},
                           json=body)
    assert r.status_code == 200, r.text


async def node_key(backend: httpx.AsyncClient, node: str = NODE) -> str:
    r = await backend.post("/v1/keys", headers={"Authorization": f"Bearer {MASTER}"},
                           json={"scopes": ["ingest"], "label": node, "node_id": node})
    assert r.status_code in (200, 201), r.text
    return r.json()["secret"]


def make_proxy(
    tmp_path: Path, seen: list[dict], vault: CredentialVault | None,
    tools: frozenset[str] = frozenset({"web_search"}),
) -> httpx.AsyncClient:
    upstream_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=make_upstream(seen)),
        base_url="http://upstream.test",
    )
    state = ProxyState(
        tools={"web_search": ENDPOINT, "plain": "http://upstream.test/plain"},
        trace_dir=tmp_path, client=upstream_client,
        vault=vault, vault_tool_names=tools,
    )
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(state)),
        base_url="http://proxy.test",
    )
    client.proxy_state = state  # type: ignore[attr-defined]
    return client


@pytest.fixture
async def proxy(
    tmp_path: Path, seen: list[dict], backend: httpx.AsyncClient
) -> httpx.AsyncClient:
    vault = CredentialVault(
        "http://backend.test", ingest_key=await node_key(backend),
        client=backend, creds_token="creds-tok",
    )
    return make_proxy(tmp_path, seen, vault)


async def arm(proxy: httpx.AsyncClient) -> str:
    r = await proxy.post("/axor/runs", json={"scenario": "vault", "faults": []})
    assert r.status_code == 201
    return r.json()["run_id"]


def trace(trace_dir: Path, run_id: str) -> list[dict]:
    path = trace_dir / f"{run_id}.jsonl"
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


# ── the credential the agent never holds ─────────────────────────────────────

async def test_the_credential_is_injected_at_the_sink(
    proxy: httpx.AsyncClient, backend: httpx.AsyncClient, seen: list[dict]
) -> None:
    await enroll(backend)
    await arm(proxy)
    r = await proxy.get("/t/web_search/", params={"q": "x"})
    assert r.status_code == 200
    assert seen[0]["auth"] == f"Bearer {SECRET}"


async def test_the_agents_own_header_is_replaced_not_merged(
    proxy: httpx.AsyncClient, backend: httpx.AsyncClient, seen: list[dict]
) -> None:
    """"The agent's config holds vault references, never keys" — so on a
    vault-mode tool the agent's own Authorization is what is being removed, not
    a fallback the vault might lose to."""
    await enroll(backend)
    await arm(proxy)
    await proxy.get("/t/web_search/", headers={"Authorization": "Bearer agent-own"})
    assert seen[0]["auth"] == f"Bearer {SECRET}"


async def test_a_tool_that_is_not_in_vault_mode_is_still_byte_for_byte(
    proxy: httpx.AsyncClient, seen: list[dict]
) -> None:
    """Section 6 stays literally true for every tool not opted in."""
    await arm(proxy)
    await proxy.get("/t/plain/", headers={"Authorization": "Bearer agent-own"})
    assert seen[0]["auth"] == "Bearer agent-own"


async def test_the_enrolled_header_and_scheme_are_honoured(
    proxy: httpx.AsyncClient, backend: httpx.AsyncClient, seen: list[dict]
) -> None:
    """An API-key tool enrolls its own header and an empty scheme."""
    await enroll(backend, header="X-Api-Key", scheme="")
    await arm(proxy)
    await proxy.get("/t/web_search/")
    assert seen[0]["api_key"] == SECRET
    assert seen[0]["auth"] is None


# ── fail closed, every way it can fail (decision #14) ────────────────────────

class TestFailClosed:
    """"Vault down → injection impossible → typed denial." Never a passthrough
    without the credential, never a cached one: a deny is enforcement working.
    The upstream must not be reached at all — a call that went out unauthorized
    is the failure this feature exists to prevent."""

    async def test_nothing_enrolled(
        self, proxy: httpx.AsyncClient, seen: list[dict]
    ) -> None:
        await arm(proxy)
        r = await proxy.get("/t/web_search/")
        assert r.status_code == 403
        assert r.json()["error"] == "credential_denied"
        assert seen == []

    async def test_out_of_this_nodes_scope(
        self, proxy: httpx.AsyncClient, backend: httpx.AsyncClient, seen: list[dict]
    ) -> None:
        await enroll(backend, scope_nodes=["some-other-node"])
        await arm(proxy)
        r = await proxy.get("/t/web_search/")
        assert r.status_code == 403
        assert "scope mismatch" in r.json()["detail"]
        assert seen == []

    async def test_revoked(
        self, proxy: httpx.AsyncClient, backend: httpx.AsyncClient, seen: list[dict]
    ) -> None:
        await enroll(backend)
        await backend.post("/v1/vault/creds/revoke",
                           headers={"Authorization": f"Bearer {MASTER}", **CREDS},
                           json={"tool": "web_search", "endpoint": ENDPOINT})
        await arm(proxy)
        r = await proxy.get("/t/web_search/")
        assert r.status_code == 403
        assert "revoked" in r.json()["detail"]
        assert seen == []

    async def test_the_vault_is_unreachable(
        self, tmp_path: Path, seen: list[dict]
    ) -> None:
        dead = CredentialVault("http://127.0.0.1:1/nope", client=None, creds_token="")
        proxy = make_proxy(tmp_path / "dead", seen, dead)
        await arm(proxy)
        r = await proxy.get("/t/web_search/")
        assert r.status_code == 403
        assert "unreachable" in r.json()["detail"]
        assert seen == []

    async def test_vault_mode_without_a_backend_does_not_pass_through(
        self, tmp_path: Path, seen: list[dict]
    ) -> None:
        """A misconfigured proxy denies rather than sending the call out
        unauthenticated."""
        proxy = make_proxy(tmp_path / "nb", seen, None)
        await arm(proxy)
        r = await proxy.get("/t/web_search/")
        assert r.status_code == 403
        assert "no backend" in r.json()["detail"]
        assert seen == []


# ── no cache, ever (decision #14) ────────────────────────────────────────────

async def test_a_revocation_takes_effect_on_the_very_next_call(
    proxy: httpx.AsyncClient, backend: httpx.AsyncClient, seen: list[dict]
) -> None:
    """A TTL cache "would reintroduce the secret-on-proxy this feature exists to
    remove", so the credential is fetched at call time and held nowhere. The
    observable consequence is that revoking during an incident stops the very
    next call, not the next process."""
    await enroll(backend)
    await arm(proxy)
    assert (await proxy.get("/t/web_search/")).status_code == 200
    await backend.post("/v1/vault/creds/revoke",
                       headers={"Authorization": f"Bearer {MASTER}", **CREDS},
                       json={"tool": "web_search", "endpoint": ENDPOINT})
    assert (await proxy.get("/t/web_search/")).status_code == 403
    assert len(seen) == 1


# ── what the trace says ──────────────────────────────────────────────────────

async def test_the_injection_is_recorded_without_the_key_material(
    proxy: httpx.AsyncClient, backend: httpx.AsyncClient, tmp_path: Path
) -> None:
    """"Every injection is a recorded event (tool, key id — never key
    material)": replay can show the call was made WITH a credential injected,
    and the trace still holds no secret."""
    await enroll(backend)
    run_id = await arm(proxy)
    await proxy.get("/t/web_search/")
    lines = trace(tmp_path, run_id)
    call = next(e for e in lines if e["kind"] == EventKind.TOOL_CALL.value)
    assert call["payload"]["credential"] == {
        "injected": True, "endpoint": ENDPOINT,
        "header": "Authorization", "version": 1,
    }
    assert SECRET not in json.dumps(lines)


async def test_a_denial_is_recorded_as_one(
    proxy: httpx.AsyncClient, tmp_path: Path
) -> None:
    run_id = await arm(proxy)
    await proxy.get("/t/web_search/")
    lines = trace(tmp_path, run_id)
    call = next(e for e in lines if e["kind"] == EventKind.TOOL_CALL.value)
    assert call["payload"]["credential"]["injected"] is False
    denial = next(e for e in lines if e["kind"] == EventKind.DENIAL.value)
    assert denial["payload"]["category"] == "vault"


async def test_a_stdio_tool_in_vault_mode_is_denied_not_passed_through(
    tmp_path: Path, seen: list[dict], backend: httpx.AsyncClient
) -> None:
    """A stdio MCP server has no request headers to inject into. Letting the
    call through unauthenticated would be the fail-open the whole feature
    exists to remove, so it is a denial with the reason — never a quiet
    downgrade to passthrough."""
    from axor_proxy.stdio_mcp import StdioMcpServer

    vault = CredentialVault(
        "http://backend.test", ingest_key=await node_key(backend),
        client=backend, creds_token="creds-tok",
    )
    # Never started: the denial happens before the upstream is touched, which
    # is the property under test.
    state = ProxyState(
        tools={"web_search": StdioMcpServer(["true"])},
        trace_dir=tmp_path / "stdio",
        vault=vault, vault_tool_names=frozenset({"web_search"}),
    )
    proxy = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(state)),
        base_url="http://proxy.test",
    )
    await enroll(backend)
    await arm(proxy)
    r = await proxy.post("/t/web_search/", json={})
    assert r.status_code == 403
    assert "stdio" in r.json()["detail"]


# ── the dispense says what it is for, and can prove it ───────────────────────

SEED = "0b" * 32


async def test_the_dispense_names_the_run_it_belongs_to(
    proxy: httpx.AsyncClient, backend: httpx.AsyncClient
) -> None:
    """A node-bound credential used to be a standing licence to drain its scope
    with nothing tying any of it to work the node did. Every fetch now leaves a
    row naming the run."""
    await enroll(backend)
    run_id = await arm(proxy)
    await proxy.get("/t/web_search/")
    log = (await backend.get("/v1/vault/creds/audit",
                             headers={"Authorization": f"Bearer {MASTER}", **CREDS})).json()
    assert len(log) == 1
    assert log[0]["run_id"] == run_id
    assert log[0]["node_id"] == NODE and log[0]["tool"] == "web_search"


async def test_an_observe_only_proxy_states_no_verdict(
    proxy: httpx.AsyncClient, backend: httpx.AsyncClient
) -> None:
    """It never gates, so it has no kernel decision to attest. Claiming `pass`
    would assert an approval nothing computed."""
    await enroll(backend)
    await arm(proxy)
    await proxy.get("/t/web_search/")
    log = (await backend.get("/v1/vault/creds/audit",
                             headers={"Authorization": f"Bearer {MASTER}", **CREDS})).json()
    assert log[0]["verdict"] is None


async def test_a_seeded_proxy_signs_and_the_plane_verifies(
    tmp_path: Path, seen: list[dict], backend: httpx.AsyncClient
) -> None:
    """The two sides build the signed envelope independently — the proxy with
    axor-core's JCS, the plane with the backend's, which delegates to the same
    kernel. This is where those bytes are proved equal in practice."""
    from nacl.signing import SigningKey

    pub = SigningKey(bytes.fromhex(SEED)).verify_key.encode().hex()
    r = await backend.post(
        "/v1/vault/creds/node-keys",
        headers={"Authorization": f"Bearer {MASTER}", **CREDS},
        json={"node_id": NODE, "public_key_hex": pub})
    assert r.status_code == 200, r.text

    vault = CredentialVault(
        "http://backend.test", ingest_key=await node_key(backend),
        client=backend, creds_token="creds-tok", signing_seed=SEED,
    )
    proxy = make_proxy(tmp_path / "signed", seen, vault)
    await enroll(backend)
    await arm(proxy)
    assert (await proxy.get("/t/web_search/")).status_code == 200
    log = (await backend.get("/v1/vault/creds/audit",
                             headers={"Authorization": f"Bearer {MASTER}", **CREDS})).json()
    assert log[0]["signed"] is True


async def test_an_unseeded_proxy_is_refused_once_the_node_has_a_key(
    proxy: httpx.AsyncClient, backend: httpx.AsyncClient, seen: list[dict]
) -> None:
    """Registering a key is the deployment saying this node signs; after that an
    unsigned fetch is a downgrade, and fail-closed means no call."""
    from nacl.signing import SigningKey

    await backend.post(
        "/v1/vault/creds/node-keys",
        headers={"Authorization": f"Bearer {MASTER}", **CREDS},
        json={"node_id": NODE,
              "public_key_hex": SigningKey(bytes.fromhex(SEED)).verify_key.encode().hex()})
    await enroll(backend)
    await arm(proxy)
    r = await proxy.get("/t/web_search/")
    assert r.status_code == 403
    assert seen == []


# ── envelope mode: the plane holds what it cannot read ───────────────────────

class TestEnvelopeMode:
    """§14.2 ships the vault self-hosted-first and names the condition for
    hosted: envelope encryption with customer-held root keys. Registering a
    sealing pubkey is that switch — after it the plane stores a `sealed_secret`
    it has no key for, and the node opens it at the sink.

    The backend is not trusted to decline to look. It is unable to.
    """

    @staticmethod
    async def _envelope(backend: httpx.AsyncClient) -> tuple[str, str]:
        from axor_proxy.vault import generate_sealing_key

        seed, public = generate_sealing_key()
        r = await backend.post(
            "/v1/vault/creds/sealing-key",
            headers={"Authorization": f"Bearer {MASTER}", **CREDS},
            json={"public_key_hex": public})
        assert r.status_code == 200, r.text
        return seed, public

    async def test_a_sealed_credential_round_trips_to_the_sink(
        self, tmp_path: Path, seen: list[dict], backend: httpx.AsyncClient
    ) -> None:
        from axor_proxy.vault import seal

        seed, public = await self._envelope(backend)
        await enroll(backend, secret="", sealed_secret=seal(public, SECRET))
        vault = CredentialVault(
            "http://backend.test", ingest_key=await node_key(backend),
            client=backend, creds_token="creds-tok", sealing_seed=seed,
        )
        proxy = make_proxy(tmp_path / "env", seen, vault)
        await arm(proxy)
        assert (await proxy.get("/t/web_search/")).status_code == 200
        assert seen[0]["auth"] == f"Bearer {SECRET}"

    async def test_the_plane_never_holds_the_plaintext(
        self, backend: httpx.AsyncClient
    ) -> None:
        from axor_proxy.vault import seal

        _, public = await self._envelope(backend)
        await enroll(backend, secret="", sealed_secret=seal(public, SECRET))
        health = await backend.get(
            "/v1/vault/creds/health",
            headers={"Authorization": f"Bearer {MASTER}", **CREDS})
        assert SECRET not in health.text
        assert health.json()["enrolled"][0]["sealed"] is True

    async def test_plaintext_enrolment_is_refused_once_sealing_is_on(
        self, backend: httpx.AsyncClient
    ) -> None:
        """A mode you can silently fall out of is not a custody boundary."""
        await self._envelope(backend)
        r = await backend.post(
            "/v1/vault/creds/enroll",
            headers={"Authorization": f"Bearer {MASTER}", **CREDS},
            json={"tool": "web_search", "endpoint": ENDPOINT,
                  "secret": SECRET, "scope_nodes": [NODE]})
        assert r.status_code == 400
        assert "sealed_secret" in r.json()["detail"]

    async def test_a_node_without_the_sealing_key_is_denied_not_served(
        self, tmp_path: Path, seen: list[dict], backend: httpx.AsyncClient
    ) -> None:
        from axor_proxy.vault import seal

        _, public = await self._envelope(backend)
        await enroll(backend, secret="", sealed_secret=seal(public, SECRET))
        vault = CredentialVault(
            "http://backend.test", ingest_key=await node_key(backend),
            client=backend, creds_token="creds-tok", sealing_seed="",
        )
        proxy = make_proxy(tmp_path / "nokey", seen, vault)
        await arm(proxy)
        r = await proxy.get("/t/web_search/")
        assert r.status_code == 403
        assert "no sealing key" in r.json()["detail"]
        assert seen == []

    async def test_the_wrong_sealing_key_denies_rather_than_calls(
        self, tmp_path: Path, seen: list[dict], backend: httpx.AsyncClient
    ) -> None:
        from axor_proxy.vault import generate_sealing_key, seal

        _, public = await self._envelope(backend)
        await enroll(backend, secret="", sealed_secret=seal(public, SECRET))
        other_seed, _ = generate_sealing_key()
        vault = CredentialVault(
            "http://backend.test", ingest_key=await node_key(backend),
            client=backend, creds_token="creds-tok", sealing_seed=other_seed,
        )
        proxy = make_proxy(tmp_path / "wrong", seen, vault)
        await arm(proxy)
        r = await proxy.get("/t/web_search/")
        assert r.status_code == 403
        assert "does not open" in r.json()["detail"]
        assert seen == []
