"""M6: Federation Vault (spec v2 Ch.5) — two secrets, two subsystems, one wall.

Tool creds: federation-scoped custody, DISPENSED, per-node scope checked at
dispense, fail-closed. Signing keys: custodied, SIGNED-not-surrendered,
per-request authorization, audited. Separate credentials to reach each.
"""
from __future__ import annotations

import ast
import asyncio
import base64
import pathlib

import httpx
import pytest
from axor_backend.app import create_app
from nacl.signing import VerifyKey

CREDS_TOKEN = "creds-secret"
SIGNING_TOKEN = "signing-secret"


@pytest.fixture
async def client(tmp_path: pathlib.Path) -> httpx.AsyncClient:
    app = create_app(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/axor.db",
        operator_keys={},
        allow_unsigned=True,
        vault_creds_token=CREDS_TOKEN,
        vault_signing_token=SIGNING_TOKEN,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://backend.test"
    ) as c:
        async with app.router.lifespan_context(app):
            yield c


CH = {"x-vault-creds-token": CREDS_TOKEN}
SH = {"x-vault-signing-token": SIGNING_TOKEN}


# ── tool credentials: dispensed, scoped, fail-closed ───────────────────────────


async def test_dispense_respects_per_node_scope(client: httpx.AsyncClient) -> None:
    await client.post("/v1/vault/creds/enroll", headers=CH, json={
        "tool": "payments", "endpoint": "https://pay.example",
        "secret": "sk_live_1", "scope_nodes": ["billing-agent"],
    })
    ok = await client.post("/v1/vault/creds/dispense", headers=CH, json={
        "node_id": "billing-agent", "tool": "payments",
        "endpoint": "https://pay.example",
    })
    assert ok.status_code == 200 and ok.json()["secret"] == "sk_live_1"

    # shared storage, partitioned access: the scraper cannot pull payments
    denied = await client.post("/v1/vault/creds/dispense", headers=CH, json={
        "node_id": "web-scraper", "tool": "payments",
        "endpoint": "https://pay.example",
    })
    assert denied.status_code == 403
    assert "scope mismatch" in denied.json()["detail"]


async def test_dispense_fails_closed_on_missing(client: httpx.AsyncClient) -> None:
    r = await client.post("/v1/vault/creds/dispense", headers=CH, json={
        "node_id": "n", "tool": "ghost", "endpoint": "https://x",
    })
    assert r.status_code == 403  # typed denial, never a silent empty secret


async def test_rotate_bumps_version_revoke_narrows(client: httpx.AsyncClient) -> None:
    await client.post("/v1/vault/creds/enroll", headers=CH, json={
        "tool": "search", "endpoint": "https://s", "secret": "v1",
        "scope_nodes": ["n"],
    })
    rot = (await client.post("/v1/vault/creds/rotate", headers=CH, json={
        "tool": "search", "endpoint": "https://s", "secret": "v2",
    })).json()
    assert rot["version"] == 2
    await client.post("/v1/vault/creds/revoke", headers=CH, json={
        "tool": "search", "endpoint": "https://s",
    })
    denied = await client.post("/v1/vault/creds/dispense", headers=CH, json={
        "node_id": "n", "tool": "search", "endpoint": "https://s",
    })
    assert denied.status_code == 403 and "revoked" in denied.json()["detail"]


# ── signing custody: sign, never surrender ─────────────────────────────────────


async def test_create_key_returns_public_half_only(client: httpx.AsyncClient) -> None:
    r = (await client.post("/v1/vault/signing/keys", headers=SH, json={
        "key_id": "fed-main", "operators": ["op_dmitrii"],
    })).json()
    assert "public_key_hex" in r
    assert "seed" not in str(r) and "seed_hex" not in r
    listed = (await client.get("/v1/vault/signing/keys", headers=SH)).json()
    assert all("seed_hex" not in k for k in listed)


async def test_sign_authorized_verifies_and_audits(client: httpx.AsyncClient) -> None:
    created = (await client.post("/v1/vault/signing/keys", headers=SH, json={
        "key_id": "fed-a", "operators": ["op_dmitrii"],
    })).json()
    payload = b"canonical-command-bytes"
    sig = (await client.post("/v1/vault/signing/sign", headers=SH, json={
        "key_id": "fed-a", "operator": "op_dmitrii",
        "payload_b64": base64.b64encode(payload).decode(),
    })).json()
    VerifyKey(bytes.fromhex(created["public_key_hex"])).verify(
        payload, bytes.fromhex(sig["signature_hex"])
    )  # raises on mismatch
    audit = (await client.get("/v1/vault/signing/audit", headers=SH)).json()
    assert audit[-1]["operator"] == "op_dmitrii" and audit[-1]["granted"] is True
    assert audit[-1]["payload_sha256"]


async def test_unauthorized_operator_refused_and_audited(
    client: httpx.AsyncClient,
) -> None:
    await client.post("/v1/vault/signing/keys", headers=SH, json={
        "key_id": "fed-b", "operators": ["op_dmitrii"],
    })
    r = await client.post("/v1/vault/signing/sign", headers=SH, json={
        "key_id": "fed-b", "operator": "op_mallory",
        "payload_b64": base64.b64encode(b"x").decode(),
    })
    assert r.status_code == 403
    audit = (await client.get("/v1/vault/signing/audit", headers=SH)).json()
    assert audit[-1] == {**audit[-1], "operator": "op_mallory", "granted": False}


# ── the wall ───────────────────────────────────────────────────────────────────


async def test_creds_token_cannot_reach_signing_and_vice_versa(
    client: httpx.AsyncClient,
) -> None:
    r = await client.post("/v1/vault/signing/sign",
                          headers={"x-vault-signing-token": CREDS_TOKEN},
                          json={"key_id": "k", "operator": "o", "payload_b64": ""})
    assert r.status_code == 403
    r = await client.post("/v1/vault/creds/dispense",
                          headers={"x-vault-creds-token": SIGNING_TOKEN},
                          json={"node_id": "n", "tool": "t", "endpoint": "e"})
    assert r.status_code == 403


def test_import_wall_between_the_two_subsystems() -> None:
    """Ch.5 §3, enforced: neither vault module imports the other. A single
    module spanning both would be the single-point-of-forgery the design
    exists to avoid."""
    src = pathlib.Path(__file__).parents[1] / "src" / "axor_backend"
    for a, b in (("vault_creds.py", "vault_signing"),
                 ("vault_signing.py", "vault_creds")):
        tree = ast.parse((src / a).read_text("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not any(b in n.name for n in node.names), f"{a} imports {b}"
            if isinstance(node, ast.ImportFrom):
                assert b not in (node.module or ""), f"{a} imports {b}"


# ── dispense is bound to the caller, not to what it says about itself ─────────

MASTER = "master-token"


@pytest.fixture
async def authed(tmp_path: pathlib.Path) -> httpx.AsyncClient:
    """A deployment with auth ON, so requests carry a principal."""
    app = create_app(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/authed.db",
        operator_keys={}, allow_unsigned=True, api_token=MASTER,
        vault_creds_token=CREDS_TOKEN, vault_signing_token=SIGNING_TOKEN,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://backend.test"
    ) as c, app.router.lifespan_context(app):
        yield c


def _as(token: str, *extra: dict) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    for e in extra:
        headers.update(e)
    return headers


async def _node_key(client: httpx.AsyncClient, node: str) -> str:
    r = await client.post("/v1/keys", headers=_as(MASTER), json={
        "scopes": ["ingest"], "label": node, "node_id": node})
    assert r.status_code in (200, 201), r.text
    return r.json()["secret"]


class TestDispenseIsBoundToTheCaller:
    """The module's central claim — "a compromised web-scraper cannot pull the
    payments credential just because both live in the same vault" — was not
    enforced anywhere. `node_id` came from the BODY, so the per-node scope was
    checked against a name the caller chose for itself: the scraper's own
    node-bound key, naming `payments-agent`, was handed the payments secret,
    while the same key naming itself was refused.

    The rule is `auth.Principal.may_speak_for`, the one the plane already
    applies where the node is in the PATH and therefore not the caller's to
    choose. One rule, one implementation.
    """

    @staticmethod
    async def _enrolled(client: httpx.AsyncClient) -> None:
        r = await client.post("/v1/vault/creds/enroll", headers=_as(MASTER, CH), json={
            "tool": "stripe", "endpoint": "https://api.stripe.com",
            "secret": "sk_live_TOPSECRET", "scope_nodes": ["payments-agent"]})
        assert r.status_code == 200, r.text

    async def test_a_node_bound_key_cannot_name_another_node(
        self, authed: httpx.AsyncClient
    ) -> None:
        await self._enrolled(authed)
        scraper = await _node_key(authed, "web-scraper")
        r = await authed.post("/v1/vault/creds/dispense", headers=_as(scraper, CH),
                              json={"node_id": "payments-agent", "tool": "stripe",
                                    "endpoint": "https://api.stripe.com"})
        assert r.status_code == 403
        assert "may not dispense credentials for 'payments-agent'" in r.json()["detail"]
        assert "sk_live" not in r.text

    async def test_the_node_it_is_bound_to_still_gets_its_own_credential(
        self, authed: httpx.AsyncClient
    ) -> None:
        await self._enrolled(authed)
        payments = await _node_key(authed, "payments-agent")
        r = await authed.post("/v1/vault/creds/dispense", headers=_as(payments, CH),
                              json={"node_id": "payments-agent", "tool": "stripe",
                                    "endpoint": "https://api.stripe.com"})
        assert r.status_code == 200
        assert r.json()["secret"] == "sk_live_TOPSECRET"

    async def test_an_unbound_credential_still_speaks_for_the_fleet(
        self, authed: httpx.AsyncClient
    ) -> None:
        """Same latitude the plane gives an unbound key for telemetry — which is
        why a node's credential belongs behind a node-bound key."""
        await self._enrolled(authed)
        r = await authed.post("/v1/vault/creds/dispense", headers=_as(MASTER, CH),
                              json={"node_id": "payments-agent", "tool": "stripe",
                                    "endpoint": "https://api.stripe.com"})
        assert r.status_code == 200

    async def test_dispense_without_a_node_says_so(
        self, client: httpx.AsyncClient
    ) -> None:
        r = await client.post("/v1/vault/creds/dispense", headers=CH,
                              json={"tool": "t", "endpoint": "e"})
        assert r.status_code == 400
        assert "node_id" in r.json()["detail"]


# ── narrowing stays narrow ────────────────────────────────────────────────────

class TestRotateDoesNotUndoARevocation:
    """Rotation cleared `revoked`, silently. An operator who revoked a
    credential during an incident and then rotated its secret re-armed it
    federation-wide, and the answer — `{"version": 2}` — did not mention it.
    Un-revoking is granting; granting is enrollment. The old test rotated BEFORE
    revoking, so the order that breaks was never run."""

    @staticmethod
    async def _revoked(client: httpx.AsyncClient) -> None:
        await client.post("/v1/vault/creds/enroll", headers=CH, json={
            "tool": "pay", "endpoint": "e", "secret": "s1", "scope_nodes": ["n1"]})
        r = await client.post("/v1/vault/creds/revoke", headers=CH,
                              json={"tool": "pay", "endpoint": "e"})
        assert r.status_code == 200

    async def test_rotating_a_revoked_credential_is_refused(
        self, client: httpx.AsyncClient
    ) -> None:
        await self._revoked(client)
        r = await client.post("/v1/vault/creds/rotate", headers=CH, json={
            "tool": "pay", "endpoint": "e", "secret": "s2"})
        assert r.status_code == 409
        assert "revoked" in r.json()["detail"]
        assert "Re-enroll" in r.json()["detail"]

    async def test_the_credential_stays_revoked(
        self, client: httpx.AsyncClient
    ) -> None:
        await self._revoked(client)
        await client.post("/v1/vault/creds/rotate", headers=CH, json={
            "tool": "pay", "endpoint": "e", "secret": "s2"})
        r = await client.post("/v1/vault/creds/dispense", headers=CH, json={
            "node_id": "n1", "tool": "pay", "endpoint": "e"})
        assert r.status_code == 403 and "revoked" in r.json()["detail"]

    async def test_re_enrolling_is_the_grant_path(
        self, client: httpx.AsyncClient
    ) -> None:
        await self._revoked(client)
        await client.post("/v1/vault/creds/enroll", headers=CH, json={
            "tool": "pay", "endpoint": "e", "secret": "s3", "scope_nodes": ["n1"]})
        r = await client.post("/v1/vault/creds/dispense", headers=CH, json={
            "node_id": "n1", "tool": "pay", "endpoint": "e"})
        assert r.status_code == 200 and r.json()["secret"] == "s3"


class TestAnEnrollmentNobodyCanUseIsRefused:
    """A fail-closed vault must not be quiet about storing something useless."""

    @pytest.mark.parametrize(("body", "says"), [
        ({"endpoint": "e", "secret": "s", "scope_nodes": ["n"]}, "tool"),
        ({"tool": "t", "secret": "s", "scope_nodes": ["n"]}, "endpoint"),
        ({"tool": "t", "endpoint": "e", "scope_nodes": ["n"]}, "secret"),
        ({"tool": "t", "endpoint": "e", "secret": "s"}, "scope_nodes"),
        ({"tool": "t", "endpoint": "e", "secret": "s", "scope_nodes": []}, "scope_nodes"),
    ])
    async def test_refused_with_the_reason(
        self, client: httpx.AsyncClient, body: dict, says: str
    ) -> None:
        r = await client.post("/v1/vault/creds/enroll", headers=CH, json=body)
        assert r.status_code == 400, r.text
        assert says in r.json()["detail"]


# ── the audit is a record, not a sample ───────────────────────────────────────

class TestEveryRequestIsAudited:
    """"Unconditionally" had two holes. A request naming a key that does not
    exist left no trace at all — so probing key ids was the one way to touch a
    custody service invisibly, while a wrong-operator attempt was recorded. And
    the log was a blob loaded and stored back, so concurrent signatures erased
    each other's rows."""

    @staticmethod
    async def _key(client: httpx.AsyncClient) -> None:
        r = await client.post("/v1/vault/signing/keys", headers=SH,
                              json={"key_id": "k1", "operators": ["alice"]})
        assert r.status_code == 200, r.text

    async def test_an_unknown_key_leaves_a_row(
        self, client: httpx.AsyncClient
    ) -> None:
        await self._key(client)
        r = await client.post("/v1/vault/signing/sign", headers=SH, json={
            "operator": "mallory", "key_id": "does-not-exist", "payload_b64": "AAAA"})
        assert r.status_code == 403
        audit = (await client.get("/v1/vault/signing/audit", headers=SH)).json()
        row = next(a for a in audit if a["key_id"] == "does-not-exist")
        assert row["granted"] is False
        assert row["refusal"] == "unknown key"

    async def test_an_unauthorized_operator_leaves_a_row(
        self, client: httpx.AsyncClient
    ) -> None:
        await self._key(client)
        await client.post("/v1/vault/signing/sign", headers=SH, json={
            "operator": "mallory", "key_id": "k1", "payload_b64": "AAAA"})
        audit = (await client.get("/v1/vault/signing/audit", headers=SH)).json()
        assert audit[-1]["refusal"] == "operator not authorized for this key"

    async def test_the_credential_that_arrived_is_recorded_beside_the_claim(
        self, authed: httpx.AsyncClient
    ) -> None:
        """`operator` is what the request CLAIMS — the vault can only check it
        against the key's allowlist, and naming an authorized operator is not
        being one. `principal` is the credential that actually arrived."""
        await authed.post("/v1/vault/signing/keys", headers=_as(MASTER, SH),
                          json={"key_id": "k1", "operators": ["alice"]})
        r = await authed.post("/v1/vault/signing/sign", headers=_as(MASTER, SH),
                              json={"operator": "alice", "key_id": "k1",
                                    "payload_b64": "AAAA"})
        assert r.status_code == 200
        audit = (await authed.get("/v1/vault/signing/audit",
                                  headers=_as(MASTER, SH))).json()
        assert audit[-1]["operator"] == "alice"      # claimed
        assert audit[-1]["principal"] == "master"    # arrived


class TestThePayloadIsDecodedStrictly:
    """`b64decode` without validate=True DISCARDS characters outside the base64
    alphabet instead of failing, so "!!!!" decoded to b"" and came back as a real
    signature over the EMPTY message — indistinguishable, to the caller, from a
    signature over their data. A length not a multiple of four raised
    binascii.Error straight out of the handler: a 500 for a bad request."""

    @pytest.mark.parametrize("payload", ["a", "!!!!", "====", "AA=A"])
    async def test_garbage_is_a_400_and_signs_nothing(
        self, client: httpx.AsyncClient, payload: str
    ) -> None:
        await client.post("/v1/vault/signing/keys", headers=SH,
                          json={"key_id": "k1", "operators": ["alice"]})
        r = await client.post("/v1/vault/signing/sign", headers=SH, json={
            "operator": "alice", "key_id": "k1", "payload_b64": payload})
        assert r.status_code == 400, (payload, r.status_code, r.text)
        assert "payload_b64" in r.json()["detail"]
        audit = (await client.get("/v1/vault/signing/audit", headers=SH)).json()
        assert audit == []  # nothing was signed, so nothing is claimed to be

    async def test_real_base64_still_signs(self, client: httpx.AsyncClient) -> None:
        await client.post("/v1/vault/signing/keys", headers=SH,
                          json={"key_id": "k1", "operators": ["alice"]})
        r = await client.post("/v1/vault/signing/sign", headers=SH, json={
            "operator": "alice", "key_id": "k1",
            "payload_b64": base64.b64encode(b"hello").decode()})
        assert r.status_code == 200


# ── concurrency: the vault stores blobs, and blobs erase each other ───────────

class TestConcurrentVaultWritesDoNotEraseEachOther:
    """Every operation here was `get_setting` then `set_setting` — two
    transactions with the whole blob in between. Measured before the fix:
    twenty concurrent enrollments left ONE credential (all twenty answered 200),
    a revoke racing a rotate left the credential live after the operator had
    been told it was revoked, and twenty-five signatures left ONE audit row."""

    async def test_every_concurrent_enrollment_survives(
        self, client: httpx.AsyncClient
    ) -> None:
        await asyncio.gather(*[
            client.post("/v1/vault/creds/enroll", headers=CH, json={
                "tool": f"t{i}", "endpoint": "e", "secret": "s",
                "scope_nodes": ["n1"]})
            for i in range(20)
        ])
        health = (await client.get("/v1/vault/creds/health", headers=CH)).json()
        assert len(health["enrolled"]) == 20

    async def test_a_revoke_racing_a_rotate_is_not_lost(
        self, client: httpx.AsyncClient
    ) -> None:
        """The one that matters: revoke is the narrowing an operator reaches for
        during an incident, and it was the write that could vanish."""
        await client.post("/v1/vault/creds/enroll", headers=CH, json={
            "tool": "pay", "endpoint": "e", "secret": "s1", "scope_nodes": ["n1"]})
        await asyncio.gather(
            client.post("/v1/vault/creds/revoke", headers=CH,
                        json={"tool": "pay", "endpoint": "e"}),
            client.post("/v1/vault/creds/rotate", headers=CH,
                        json={"tool": "pay", "endpoint": "e", "secret": "s2"}),
        )
        r = await client.post("/v1/vault/creds/dispense", headers=CH, json={
            "node_id": "n1", "tool": "pay", "endpoint": "e"})
        assert r.status_code == 403 and "revoked" in r.json()["detail"]

    async def test_every_concurrent_signature_is_audited(
        self, client: httpx.AsyncClient
    ) -> None:
        await client.post("/v1/vault/signing/keys", headers=SH,
                          json={"key_id": "k1", "operators": ["alice"]})
        signed = await asyncio.gather(*[
            client.post("/v1/vault/signing/sign", headers=SH, json={
                "operator": "alice", "key_id": "k1", "payload_b64": "AAAA"})
            for _ in range(25)
        ])
        assert all(r.status_code == 200 for r in signed)
        audit = (await client.get("/v1/vault/signing/audit", headers=SH)).json()
        assert len(audit) == 25

    async def test_two_keys_of_the_same_id_do_not_overwrite_each_other(
        self, client: httpx.AsyncClient
    ) -> None:
        """A key id already pinned in adapter config must not be replaced: every
        command signed with the old half would start failing verification at the
        node. The existence check and the write are one mutation now."""
        results = await asyncio.gather(*[
            client.post("/v1/vault/signing/keys", headers=SH,
                        json={"key_id": "fed", "operators": [f"op{i}"]})
            for i in range(8)
        ])
        assert sum(r.status_code == 200 for r in results) == 1
        assert sum(r.status_code == 409 for r in results) == 7
        keys = (await client.get("/v1/vault/signing/keys", headers=SH)).json()
        assert len(keys) == 1


def test_the_subsystem_tokens_are_not_compared_byte_by_byte() -> None:
    """`_gate` compared the vault tokens with `!=`, which returns at the first
    differing byte, while `constant_time_eq` guarded the API token and every
    scoped key in security.py. These are the two tokens in front of credential
    dispense and delegated signing.

    A source property, so checked in the source — the same shape as the import
    wall above, and for the same reason: a timing assertion would be flaky, and
    the thing that must not come back is the `==`.
    """
    src = pathlib.Path(__file__).parents[1] / "src" / "axor_backend"
    tree = ast.parse((src / "routers" / "vault.py").read_text("utf-8"))
    gate = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_gate"
    )
    calls = {
        n.func.id for n in ast.walk(gate)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "constant_time_eq" in calls, "_gate must compare in constant time"
    equality = [
        n for n in ast.walk(gate)
        if isinstance(n, ast.Compare)
        and any(isinstance(op, (ast.Eq, ast.NotEq)) for op in n.ops)
    ]
    assert not equality, "_gate compares a secret with ==/!= again"
