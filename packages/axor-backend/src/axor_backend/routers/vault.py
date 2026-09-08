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

from fastapi import APIRouter, HTTPException, Request

from axor_backend.auth import constant_time_eq
from axor_backend.clock import now
from axor_backend.deps import StoreDep
from axor_backend.vault_dispense import (
    DISPENSE_LOG_CAP,
    DISPENSE_LOG_SETTING,
    NODE_KEYS_SETTING,
    AttestationRefused,
)
from axor_backend.vault_dispense import log_entry as dispense_row
from axor_backend.vault_dispense import verify as verify_attestation

router = APIRouter(prefix="/v1/vault", tags=["vault"])


def _gate(request: Request, which: str) -> None:
    """Per-subsystem bearer check. A configured token is required exactly for
    its own subsystem; in the open dev posture (no token configured) the
    subsystem follows the app's global posture — which startup logs loudly.

    Compared with `constant_time_eq`, like the API token and every scoped key in
    `security.py`. A `!=` on secrets returns at the first differing byte, and
    these two are the tokens guarding credential dispense and delegated signing
    — the last place in the codebase that should compare a secret in a way that
    leaks its prefix.
    """
    expected = getattr(request.app.state.config, f"vault_{which}_token")
    if expected is None:
        return
    presented = request.headers.get(f"x-vault-{which}-token", "")
    if not constant_time_eq(presented, expected):
        raise HTTPException(403, f"vault {which}: missing or wrong token")


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
    _gate(request, "creds")
    from axor_backend.vault_creds import EnrollmentInvalid, ToolCredentialVault

    try:
        return await ToolCredentialVault(store).enroll(
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
    _gate(request, "creds")
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
    await store.mutate_setting(
        DISPENSE_LOG_SETTING,
        lambda stored: [*(stored or []), row][-DISPENSE_LOG_CAP:],
    )
    return credential


@router.post("/creds/rotate")
async def vault_rotate(body: dict, request: Request, store: StoreDep) -> dict:
    _gate(request, "creds")
    from axor_backend.vault_creds import (
        DispenseDenied,
        EnrollmentInvalid,
        NotEnrolled,
        ToolCredentialVault,
    )

    try:
        return await ToolCredentialVault(store).rotate(
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
    _gate(request, "creds")
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
    _gate(request, "creds")
    from axor_backend.vault_creds import SEALING_KEY_SETTING

    pubkey = str(body.get("public_key_hex", ""))
    if len(pubkey) != 64 or not _is_hex(pubkey):
        raise HTTPException(400, "requires public_key_hex (32-byte X25519, hex)")
    await store.set_setting(SEALING_KEY_SETTING, pubkey)
    return {"registered": True, "public_key_hex": pubkey}


@router.get("/creds/sealing-key")
async def vault_sealing_key(request: Request, store: StoreDep) -> dict:
    """The key to seal against, and whether this deployment holds plaintext."""
    _gate(request, "creds")
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
    _gate(request, "creds")
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
    _gate(request, "creds")
    return (await store.get_setting(NODE_KEYS_SETTING)) or {}


@router.get("/creds/audit")
async def vault_creds_audit(request: Request, store: StoreDep) -> list[dict]:
    """What every dispensed credential was fetched for. Never the credential."""
    _gate(request, "creds")
    return (await store.get_setting(DISPENSE_LOG_SETTING)) or []


@router.get("/creds/health")
async def vault_health(request: Request, store: StoreDep) -> dict:
    _gate(request, "creds")
    from axor_backend.vault_creds import ToolCredentialVault

    return await ToolCredentialVault(store).health()


# ── signing: custody side ─────────────────────────────────────────────────────

@router.post("/signing/keys")
async def vault_create_key(body: dict, request: Request, store: StoreDep) -> dict:
    _gate(request, "signing")
    from axor_backend.vault_signing import SigningCustody, SignRefused

    try:
        return await SigningCustody(store).create_key(
            str(body.get("key_id", "")), list(body.get("operators", [])),
        )
    except SignRefused as exc:
        raise HTTPException(409, exc.reason) from exc


@router.get("/signing/keys")
async def vault_list_keys(request: Request, store: StoreDep) -> list[dict]:
    _gate(request, "signing")
    from axor_backend.vault_signing import SigningCustody

    return await SigningCustody(store).keys_public()


@router.post("/signing/sign")
async def vault_sign(body: dict, request: Request, store: StoreDep) -> dict:
    _gate(request, "signing")
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
async def vault_audit(request: Request, store: StoreDep) -> list[dict]:
    _gate(request, "signing")
    from axor_backend.vault_signing import SigningCustody

    return await SigningCustody(store).audit()


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
