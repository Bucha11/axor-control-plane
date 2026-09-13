"""A key is a subset of the authority that mints it, and minting leaves a trace.

`POST /v1/keys` validated the requested scopes against the whole ladder rather
than against the caller's. The ladder does not imply (`Principal.may` is exact
membership), so a key holding ONLY `admin` is refused everything except this
route — and could use this route to fix that:

    it reads runs         -> 403   (holds ['admin'], needs read)
    it speaks as node-b   -> 403   (holds ['admin'], needs operate)

    it mints a key for itself -> 201
       scopes=['read', 'ingest', 'operate', 'admin'] node_id=None
    the new key reads runs      -> 200
    the new key speaks as node-b-> 404      ← not 403: the gate is behind it

Both walls this module raises — scopes and `may_speak_for` — were one POST from
optional, through the door that raises them.

And the episode left nothing. After the revoke, the key is not in the listing
(which only ever shows what still exists) and a `LIKE '%ak_10765c9b%'` across
every column of every table in the database returns zero rows. A credential
that can be issued, used and withdrawn without a record is one an operator
cannot reason about afterwards, so mint and revoke now append to the same
append-only custody log the vault writes to (`AUDIT_KIND`, storage migration
0015), never carrying the secret or its hash.
"""
from __future__ import annotations

import logging
import pathlib

import httpx
import pytest
from axor_backend.app import create_app

TOKEN = "master-secret"
H = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
async def client(tmp_path: pathlib.Path) -> httpx.AsyncClient:
    app = create_app(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/axor.db",
        operator_keys={}, allow_unsigned=True, api_token=TOKEN,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://backend.test",
    ) as c, app.router.lifespan_context(app):
        yield c


async def _mint(client: httpx.AsyncClient, headers: dict, **body: object) -> httpx.Response:
    return await client.post("/v1/keys", json=body, headers=headers)


class TestAKeyCannotMintMoreThanItHolds:
    async def test_admin_only_key_cannot_widen_itself(
        self, client: httpx.AsyncClient,
    ) -> None:
        minted = (await _mint(client, H, scopes=["admin"])).json()
        kh = {"Authorization": f"Bearer {minted['secret']}"}
        # It is refused `read` on every other route; it may not buy it here.
        assert (await client.get("/v1/runs", headers=kh)).status_code == 403
        r = await _mint(client, kh, scopes=["read", "ingest", "operate", "admin"])
        assert r.status_code == 403
        assert "may not delegate ['ingest', 'operate', 'read']" in r.json()["detail"]

    async def test_it_cannot_mint_even_one_scope_it_lacks(
        self, client: httpx.AsyncClient,
    ) -> None:
        """`read` is the default body, so the plain mint is the escalation."""
        minted = (await _mint(client, H, scopes=["admin"])).json()
        kh = {"Authorization": f"Bearer {minted['secret']}"}
        r = await client.post("/v1/keys", json={}, headers=kh)
        assert r.status_code == 403
        assert "may not delegate ['read']" in r.json()["detail"]

    async def test_it_may_mint_what_it_does_hold(
        self, client: httpx.AsyncClient,
    ) -> None:
        minted = (await _mint(client, H, scopes=["admin"])).json()
        kh = {"Authorization": f"Bearer {minted['secret']}"}
        r = await _mint(client, kh, scopes=["admin"])
        assert r.status_code == 201
        assert r.json()["scopes"] == ["admin"]

    async def test_the_operator_delegates_freely(
        self, client: httpx.AsyncClient,
    ) -> None:
        """The master token holds the whole ladder, so nothing here narrows it."""
        r = await _mint(client, H, scopes=["read", "ingest", "operate", "admin"],
                        node_id="node-z")
        assert r.status_code == 201
        assert sorted(r.json()["scopes"]) == ["admin", "ingest", "operate", "read"]
        assert r.json()["node_id"] == "node-z"


class TestANodeBoundKeyMintsOnlyForItsOwnNode:
    async def test_it_cannot_mint_for_another_node(
        self, client: httpx.AsyncClient,
    ) -> None:
        minted = (await _mint(client, H, scopes=["admin"], node_id="node-a")).json()
        kh = {"Authorization": f"Bearer {minted['secret']}"}
        r = await _mint(client, kh, scopes=["admin"], node_id="node-b")
        assert r.status_code == 403
        assert "bound to node 'node-a'" in r.json()["detail"]

    async def test_an_unnamed_node_inherits_the_binding(
        self, client: httpx.AsyncClient,
    ) -> None:
        """Silence used to mean "fleet-wide", which is how the binding escaped."""
        minted = (await _mint(client, H, scopes=["admin"], node_id="node-a")).json()
        kh = {"Authorization": f"Bearer {minted['secret']}"}
        r = await _mint(client, kh, scopes=["admin"])
        assert r.status_code == 201
        assert r.json()["node_id"] == "node-a"

    async def test_an_unbound_key_still_mints_unbound(
        self, client: httpx.AsyncClient,
    ) -> None:
        minted = (await _mint(client, H, scopes=["admin"])).json()
        kh = {"Authorization": f"Bearer {minted['secret']}"}
        assert (await _mint(client, kh, scopes=["admin"])).json()["node_id"] is None


class TestTheCredentialLifecycleLeavesATrace:
    async def test_mint_and_revoke_are_both_recorded(
        self, client: httpx.AsyncClient,
    ) -> None:
        made = (await _mint(client, H, scopes=["read"], label="short-lived")).json()
        assert (await client.delete(f"/v1/keys/{made['key_id']}",
                                    headers=H)).status_code == 200
        listing = (await client.get("/v1/keys", headers=H)).json()
        assert not any(k["key_id"] == made["key_id"] for k in listing)

        audit = (await client.get("/v1/keys/audit", headers=H)).json()
        mine = [a for a in audit if a["key_id"] == made["key_id"]]
        assert [a["action"] for a in mine] == ["revoke", "mint"]  # newest first
        assert mine[1]["scopes"] == ["read"]
        assert mine[1]["label"] == "short-lived"
        assert all(a["by"] == {"kind": "master", "id": "master"} for a in mine)

    async def test_the_log_never_carries_the_credential(
        self, client: httpx.AsyncClient,
    ) -> None:
        made = (await _mint(client, H, scopes=["read"])).json()
        audit = (await client.get("/v1/keys/audit", headers=H)).json()
        assert made["secret"] not in str(audit)
        assert not any("hashed_secret" in a for a in audit)

    async def test_a_refused_mint_is_not_an_issued_key(
        self, client: httpx.AsyncClient,
    ) -> None:
        minted = (await _mint(client, H, scopes=["admin"])).json()
        kh = {"Authorization": f"Bearer {minted['secret']}"}
        await _mint(client, kh, scopes=["read", "admin"])
        audit = (await client.get("/v1/keys/audit", headers=H)).json()
        assert [a["action"] for a in audit] == ["mint"]
        assert audit[0]["key_id"] == minted["key_id"]

    async def test_the_log_is_paged_newest_first(
        self, client: httpx.AsyncClient,
    ) -> None:
        for i in range(5):
            await _mint(client, H, scopes=["read"], label=f"k{i}")
        page = (await client.get("/v1/keys/audit?limit=2", headers=H)).json()
        assert [a["label"] for a in page] == ["k4", "k3"]
        nxt = (await client.get(
            f"/v1/keys/audit?limit=2&before_id={page[-1]['audit_id']}",
            headers=H)).json()
        assert [a["label"] for a in nxt] == ["k2", "k1"]

    async def test_reading_the_log_needs_admin(
        self, client: httpx.AsyncClient,
    ) -> None:
        minted = (await _mint(client, H, scopes=["read"])).json()
        r = await client.get("/v1/keys/audit",
                             headers={"Authorization": f"Bearer {minted['secret']}"})
        assert r.status_code == 403


class TestTheMintRouteTypesItsBody:
    @pytest.mark.parametrize(("body", "detail"), [
        ({"scopes": {"admin": True}}, "scopes must be a list of strings"),
        ({"scopes": [["read"]]}, "scopes must be a list of strings"),
        ({"scopes": ["read", None]}, "scopes must be a list of strings"),
        ({"scopes": "read"}, "scopes must be a list of strings"),
        ({"scopes": []}, "scopes must be non-empty"),
        ({"scopes": ["wat"]}, "unknown scopes: ['wat']"),
        ({"scopes": ["read"], "label": {"a": 1}}, "label must be a string"),
        ({"scopes": ["read"], "label": "x" * 201},
         "label must be at most 200 characters"),
    ])
    async def test_a_bad_body_is_the_callers_fault(
        self, client: httpx.AsyncClient, body: dict, detail: str,
    ) -> None:
        """A dict minted a real admin key (its KEYS passed `set(scopes)`), and a
        nested list left the route as TypeError → 500 {"error": "internal"}."""
        r = await _mint(client, H, **body)
        assert r.status_code == 400, r.text
        assert r.json()["detail"] == detail

    async def test_no_key_survives_a_refused_body(
        self, client: httpx.AsyncClient,
    ) -> None:
        await _mint(client, H, scopes={"admin": True})
        assert (await client.get("/v1/keys", headers=H)).json() == []


class TestAuthStatusIsNotASilentOracle:
    async def test_a_bad_token_is_logged_like_any_other_refusal(
        self, client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """This route is open, so `auth_middleware` returns before it can log —
        and it is the one route that tells an anonymous caller whether a token
        is good. 500 guesses produced 500 × 200 and not one line."""
        with caplog.at_level(logging.WARNING, logger="axor.backend.auth"):
            for i in range(5):
                r = await client.get(
                    "/v1/auth/status",
                    headers={"Authorization": f"Bearer guess{i}"})
                assert r.status_code == 200
                assert r.json()["authenticated"] is False
        assert len(caplog.records) == 5
        assert "did not resolve" in caplog.records[0].message

    async def test_a_good_token_is_not_a_refusal(
        self, client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="axor.backend.auth"):
            r = await client.get("/v1/auth/status", headers=H)
        assert r.json()["authenticated"] is True
        assert caplog.records == []

    async def test_asking_without_a_token_is_not_a_refusal(
        self, client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The UI asks this on load to decide whether to prompt. Silence is a
        question, not a failed attempt."""
        with caplog.at_level(logging.WARNING, logger="axor.backend.auth"):
            r = await client.get("/v1/auth/status")
        assert r.json() == {"auth_enabled": True, "authenticated": False,
                            "scopes": []}
        assert caplog.records == []
