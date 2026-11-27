"""The federation vault — two subsystems, one wall (spec v2 Ch.5).

`creds` dispenses tool credentials to nodes; `signing` holds the ed25519 keys
operators sign plane commands with. They share the word "vault" and nothing
else. THE WALL (§3): each is reached with its OWN bearer token, in its own
header, so the ability to dispense a credential never grants the ability to
request a signature. A single admin surface spanning both would recreate the
single-point-of-forgery the design exists to avoid; CI enforces the matching
import wall between the two modules.

The scope ladder is the outer gate (auth._WRITE_POLICY splits these routes by
what they do, not by their shared prefix); the per-subsystem token is the inner
one. On the two routes that name a node or an operator, there is a third: the
request may not simply assert who it is speaking as — see `_speaking_as`.
"""
from __future__ import annotations

import base64
import binascii
import secrets
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from axor_backend.auth import constant_time_eq, hash_secret
from axor_backend.clock import now
from axor_backend.deps import PrincipalDep, StoreDep
from axor_backend.vault_dispense import AUDIT_KIND as DISPENSE_KIND
from axor_backend.vault_dispense import NODE_KEYS_SETTING, AttestationRefused
from axor_backend.vault_dispense import log_entry as dispense_row
from axor_backend.vault_dispense import verify as verify_attestation

router = APIRouter(prefix="/v1/vault", tags=["vault"])

# Rows an audit page returns by default, and the most it will return at all.
# Both logs used to be capped in storage, so "give me everything" was bounded by
# something other than this route; now it is bounded here.
AUDIT_PAGE = 200
AUDIT_PAGE_MAX = 1000


def _page(limit: int) -> int:
    """A page size the caller chose, clamped and refused rather than silently
    reinterpreted — a `limit=0` that quietly means 200 is a caller reading an
    empty log and believing it."""
    if limit < 1:
        raise HTTPException(400, "`limit` must be at least 1")
    return min(limit, AUDIT_PAGE_MAX)


async def vault_gate(request: Request, which: str) -> None:
    """Per-subsystem bearer check. A configured token is required exactly for
    its own subsystem; in the open dev posture (no token configured) the
    subsystem follows the app's global posture — which startup logs loudly.

    The deployment's token (AXOR_VAULT_<WHICH>_TOKEN) is one secret for the
    whole plane: right for a self-hosted plane, wrong for one with many
    workspaces, where it would be handed to all of them. Without it, a
    workspace may mint its own token for each subsystem (Settings → security);
    once it has, that workspace's requests need it.

    Compared with `constant_time_eq`, like the API token and every scoped key in
    `security.py`. A `!=` on secrets returns at the first differing byte, and
    these two are the tokens guarding credential dispense and delegated signing
    — the last place in the codebase that should compare a secret in a way that
    leaks its prefix.
    """
    presented = request.headers.get(f"x-vault-{which}-token", "")
    expected = getattr(request.app.state.config, f"vault_{which}_token")
    if expected is not None:
        if not constant_time_eq(presented, expected):
            raise HTTPException(403, f"vault {which}: missing or wrong token")
        return
    own = (await workspace_tokens(request.app.state.store)).get(which)
    if own and not constant_time_eq(hash_secret(presented), own["hash"]):
        raise HTTPException(403, f"vault {which}: this workspace requires its own "
                                 f"{which} token (X-Vault-{which.capitalize()}-Token)")


# ── a workspace's own vault tokens ────────────────────────────────────────────

WORKSPACE_TOKENS_SETTING = "vault_tokens/v1"
SUBSYSTEMS = ("creds", "signing")


async def workspace_tokens(store: Any) -> dict[str, dict]:  # noqa: ANN401
    """The workspace's minted tokens by subsystem: hash, when, by whom."""
    held = await store.get_setting(WORKSPACE_TOKENS_SETTING)
    return held if isinstance(held, dict) else {}


def _subsystem(which: str) -> str:
    if which not in SUBSYSTEMS:
        raise HTTPException(404, f"no vault subsystem {which!r} (creds or signing)")
    return which


@router.get("/tokens")
async def vault_tokens(request: Request, store: StoreDep) -> dict:
    """Which of the workspace's vault subsystems have a token of their own —
    never the token. With a deployment token set, that one rules instead."""
    deployment = {w: getattr(request.app.state.config, f"vault_{w}_token") is not None
                  for w in SUBSYSTEMS}
    held = await workspace_tokens(store)
    return {w: {"set": w in held, "since": held.get(w, {}).get("ts"),
                "by": held.get(w, {}).get("by"), "deployment": deployment[w]}
            for w in SUBSYSTEMS}


@router.post("/tokens/{which}", status_code=201)
async def vault_mint_token(which: str, request: Request, store: StoreDep,
                           principal: PrincipalDep) -> dict:
    """Mint (or replace) the workspace's token for one subsystem. Shown once;
    only its hash is kept. Replacing it cuts off whoever holds the old one —
    which is what a rotation after a leak needs. An owner or admin."""
    from axor_backend import audit, permissions

    permissions.require_admin(principal)
    which = _subsystem(which)
    if getattr(request.app.state.config, f"vault_{which}_token") is not None:
        raise HTTPException(409, f"this plane has a deployment {which} token, which "
                                 "rules every workspace — a workspace token would not apply")
    secret = f"axv_{which}_{secrets.token_urlsafe(32)}"
    held = await workspace_tokens(store)
    replaced = which in held
    held[which] = {"hash": hash_secret(secret), "ts": now(), "by": audit.actor(request)["who"]}
    await store.set_setting(WORKSPACE_TOKENS_SETTING, held)
    # a replacement reads apart from a first token on the trail (QA-070)
    await audit.record(store, request, "vault_token", which,
                       **({"replaced": True} if replaced else {}))
    return {"subsystem": which, "token": secret,
            "header": f"X-Vault-{which.capitalize()}-Token"}


@router.delete("/tokens/{which}")
async def vault_drop_token(which: str, request: Request, store: StoreDep,
                           principal: PrincipalDep) -> dict:
    """Drop the workspace's token: the subsystem goes back to the admin's
    scope alone. An owner or admin."""
    from axor_backend import audit, permissions

    permissions.require_admin(principal)
    which = _subsystem(which)
    held = await workspace_tokens(store)
    if held.pop(which, None) is None:
        raise HTTPException(404, f"this workspace has no {which} token")
    await store.set_setting(WORKSPACE_TOKENS_SETTING, held)
    await audit.record(store, request, "vault_token_dropped", which)
    return {"subsystem": which, "set": False}


def _speaking_as(request: Request, node_id: str) -> None:
    """Refuse a request that claims to be a node it may not speak for.

    `dispense` takes its node from the BODY, so the vault's per-node scope was
    checked against a name the caller chose for itself. The module's central
    claim — "a compromised web-scraper cannot pull the payments credential just
    because both live in the same vault" — was not enforced anywhere: the
    scraper's own node-bound key, naming `payments-agent`, was handed the
    payments secret, while the same key naming itself was refused. The check ran
    correctly on an answer the attacker supplied.

    `auth.Principal.may_speak_for` is the rule the plane already applies to
    `/v1/plane/{node}/telemetry`, where the node is in the PATH and therefore
    not the caller's to choose. Same rule, one implementation: a node-bound key
    speaks only for its own node; an unbound credential (the master token, a
    fleet-wide key, a human login) speaks for the fleet — so a node's credential
    should be fetched with a node-bound key, exactly as its telemetry is posted
    with one. With auth disabled entirely there is no principal to check, which
    is the open dev posture the startup warning names.
    """
    principal = getattr(request.state, "principal", None)
    if principal is not None and not principal.may_speak_for(node_id):
        raise HTTPException(
            403,
            f"key {principal.key_id} is bound to node {principal.node_id!r} "
            f"and may not dispense credentials for {node_id!r}",
        )


# ── credentials: dispense side ────────────────────────────────────────────────

@router.post("/creds/enroll")
async def vault_enroll(body: dict, request: Request, store: StoreDep) -> dict:
    await vault_gate(request, "creds")
    from axor_backend.vault_creds import EnrollmentInvalid, ToolCredentialVault

    try:
        return await ToolCredentialVault(
            store, plaintext_allowed=not request.app.state.config.hosted).enroll(
            str(body.get("tool", "")),
            str(body.get("endpoint", "")),
            str(body.get("secret", "")),
            [str(n) for n in body.get("scope_nodes", [])],
            header=str(body.get("header", "") or "Authorization"),
            scheme=str(body.get("scheme", "Bearer")),
            sealed_secret=str(body.get("sealed_secret", "")),
        )
    except EnrollmentInvalid as exc:
        raise HTTPException(400, exc.reason) from exc


@router.post("/creds/dispense")
async def vault_dispense(body: dict, request: Request, store: StoreDep) -> dict:
    """The one read path: a governed node fetches its OWN credential.

    Four checks, in this order, and each one is doing its own job. May this
    caller speak as the node it named (`_speaking_as`)? Does the attestation
    name the call actually being fetched, and was that call approved
    (`vault_dispense`)? Is the node in the credential's dispense scope
    (`ToolCredentialVault`)? Then, and only then, the secret — and a row saying
    what it was for.

    The scope check used to do the work of all four.
    """
    await vault_gate(request, "creds")
    from axor_backend.vault_creds import DispenseDenied, ToolCredentialVault

    node_id = str(body.get("node_id", ""))
    tool = str(body.get("tool", ""))
    endpoint = str(body.get("endpoint", ""))
    if not node_id:
        raise HTTPException(400, "dispense requires the node_id fetching the credential")
    _speaking_as(request, node_id)

    pubkeys = (await store.get_setting(NODE_KEYS_SETTING)) or {}
    attestation = body.get("attestation")
    try:
        signed = verify_attestation(
            attestation, node_id=node_id, tool=tool, endpoint=endpoint,
            pubkey_hex=pubkeys.get(node_id),
        )
    except AttestationRefused as exc:
        raise HTTPException(403, exc.reason) from exc

    try:
        credential = await ToolCredentialVault(store).dispense(
            node_id, tool, endpoint,
        )
    except DispenseDenied as exc:
        raise HTTPException(403, exc.reason) from exc

    principal = getattr(request.state, "principal", None)
    row = dispense_row(
        attestation, version=int(credential["version"]), signed=signed,
        principal=getattr(principal, "key_id", "") if principal else "",
        ts=now(),
    )
    await store.add_vault_audit(DISPENSE_KIND, row, row["ts"])
    return credential


@router.post("/creds/rotate")
async def vault_rotate(body: dict, request: Request, store: StoreDep) -> dict:
    await vault_gate(request, "creds")
    from axor_backend.vault_creds import (
        DispenseDenied,
        EnrollmentInvalid,
        NotEnrolled,
        ToolCredentialVault,
    )

    try:
        return await ToolCredentialVault(
            store, plaintext_allowed=not request.app.state.config.hosted).rotate(
            str(body.get("tool", "")),
            str(body.get("endpoint", "")),
            str(body.get("secret", "")),
            sealed_secret=str(body.get("sealed_secret", "")),
        )
    except NotEnrolled as exc:
        raise HTTPException(404, exc.reason) from exc
    except EnrollmentInvalid as exc:
        raise HTTPException(400, exc.reason) from exc
    except DispenseDenied as exc:
        # Revoked: rotating would grant it again, and granting is enrollment.
        raise HTTPException(409, exc.reason) from exc


@router.post("/creds/revoke")
async def vault_revoke(body: dict, request: Request, store: StoreDep) -> dict:
    """Narrowing only: revoke is available over the plane in an incident;
    granting is enrollment config, never a command."""
    await vault_gate(request, "creds")
    from axor_backend.vault_creds import NotEnrolled, ToolCredentialVault

    try:
        return await ToolCredentialVault(store).revoke(
            str(body.get("tool", "")), str(body.get("endpoint", "")),
        )
    except NotEnrolled as exc:
        raise HTTPException(404, exc.reason) from exc


@router.post("/creds/sealing-key")
async def vault_register_sealing_key(
    body: dict, request: Request, store: StoreDep
) -> dict:
    """Register the org's credential-sealing PUBLIC key — and stop this
    deployment storing plaintext.

    §14.2 ships the vault self-hosted-first and names the condition for hosted:
    envelope encryption with customer-held root keys. This is that switch. After
    it, `enroll` and `rotate` refuse a plaintext `secret` and take a
    `sealed_secret` the backend cannot open; the private half never comes here.

    Public by definition, so registering it is not handing us anything. What it
    buys is that we are no longer trusted to decline to look — we are unable to.
    """
    await vault_gate(request, "creds")
    from axor_backend.vault_creds import SEALING_KEY_SETTING

    pubkey = str(body.get("public_key_hex", ""))
    if len(pubkey) != 64 or not _is_hex(pubkey):
        raise HTTPException(400, "requires public_key_hex (32-byte X25519, hex)")
    await store.set_setting(SEALING_KEY_SETTING, pubkey)
    return {"registered": True, "public_key_hex": pubkey}


@router.get("/creds/sealing-key")
async def vault_sealing_key(request: Request, store: StoreDep) -> dict:
    """The key to seal against, and whether this deployment holds plaintext."""
    await vault_gate(request, "creds")
    from axor_backend.vault_creds import SEALING_KEY_SETTING

    pubkey = await store.get_setting(SEALING_KEY_SETTING)
    return {"public_key_hex": pubkey, "envelope_mode": bool(pubkey)}


@router.post("/creds/node-keys")
async def vault_register_node_key(
    body: dict, request: Request, store: StoreDep
) -> dict:
    """Register a node's ed25519 pubkey, so its dispense attestations verify.

    Config, like every other public key here: pubkeys are not secrets, and the
    one thing that makes them useful is living where verification happens. A
    node without a registered key still dispenses — its rows just read
    `signed: false`, which is the honest record of what happened.
    """
    await vault_gate(request, "creds")
    node_id = str(body.get("node_id", ""))
    pubkey = str(body.get("public_key_hex", ""))
    if not node_id or len(pubkey) != 64 or not _is_hex(pubkey):
        raise HTTPException(
            400, "requires node_id and public_key_hex (32-byte ed25519, hex)",
        )
    await store.mutate_setting(
        NODE_KEYS_SETTING, lambda stored: {**(stored or {}), node_id: pubkey},
    )
    return {"node_id": node_id, "registered": True}


@router.get("/creds/node-keys")
async def vault_node_keys(request: Request, store: StoreDep) -> dict:
    await vault_gate(request, "creds")
    return (await store.get_setting(NODE_KEYS_SETTING)) or {}


@router.get("/creds/audit")
async def vault_creds_audit(
    request: Request, store: StoreDep,
    limit: int = AUDIT_PAGE, before_id: int | None = None,
) -> list[dict]:
    """What every dispensed credential was fetched for. Never the credential.

    Newest first, paged: the log is no longer trimmed to its newest thousand on
    every append (migration 0015), so handing back all of it is no longer a
    thing something else was quietly making safe. Page with `before_id` from the
    `audit_id` of the last row you read.
    """
    await vault_gate(request, "creds")
    return await store.vault_audit_entries(
        DISPENSE_KIND, limit=_page(limit), before_id=before_id,
    )


@router.get("/creds/health")
async def vault_health(request: Request, store: StoreDep) -> dict:
    await vault_gate(request, "creds")
    from axor_backend.vault_creds import ToolCredentialVault

    return await ToolCredentialVault(store).health()


# ── signing: custody side ─────────────────────────────────────────────────────

@router.post("/signing/keys")
async def vault_create_key(body: dict, request: Request, store: StoreDep) -> dict:
    await vault_gate(request, "signing")
    from axor_backend.vault_signing import SigningCustody, SignRefused

    try:
        return await SigningCustody(store).create_key(
            str(body.get("key_id", "")), list(body.get("operators", [])),
        )
    except SignRefused as exc:
        raise HTTPException(409, exc.reason) from exc


@router.get("/signing/keys")
async def vault_list_keys(request: Request, store: StoreDep) -> list[dict]:
    await vault_gate(request, "signing")
    from axor_backend.vault_signing import SigningCustody

    return await SigningCustody(store).keys_public()


@router.post("/signing/sign")
async def vault_sign(body: dict, request: Request, store: StoreDep) -> dict:
    await vault_gate(request, "signing")
    from axor_backend.vault_signing import SigningCustody, SignRefused

    principal = getattr(request.state, "principal", None)
    try:
        return await SigningCustody(store).sign(
            str(body.get("operator", "")),
            str(body.get("key_id", "")),
            _payload(str(body.get("payload_b64", ""))),
            principal=getattr(principal, "key_id", "") if principal else "",
        )
    except SignRefused as exc:
        raise HTTPException(403, exc.reason) from exc


@router.get("/signing/audit")
async def vault_audit(
    request: Request, store: StoreDep,
    limit: int = AUDIT_PAGE, before_id: int | None = None,
) -> list[dict]:
    """Every signature request, granted or refused. Newest first, paged — see
    `/creds/audit` for why paging arrived with the table."""
    await vault_gate(request, "signing")
    from axor_backend.vault_signing import SigningCustody

    return await SigningCustody(store).audit(
        limit=_page(limit), before_id=before_id,
    )


def _payload(payload_b64: str) -> bytes:
    """Decode the bytes to be signed, strictly.

    `b64decode` without `validate=True` DISCARDS characters outside the base64
    alphabet instead of failing, so `"!!!!"` decoded to `b""` and came back as a
    real signature over the empty message — indistinguishable, to the caller,
    from a signature over their data. A length that is not a multiple of four
    raised `binascii.Error` straight out of the handler: a 500 for what is
    plainly a bad request.

    One field, two wrong behaviours and no right one. Both are 400 now: a
    signature is only meaningful over bytes the caller meant to send.
    """
    try:
        return base64.b64decode(payload_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(
            400, f"payload_b64 is not valid base64: {exc}"
        ) from exc


def _is_hex(value: str) -> bool:
    try:
        bytes.fromhex(value)
    except ValueError:
        return False
    return True
