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
one.
"""
from __future__ import annotations

import base64

from fastapi import APIRouter, HTTPException, Request

from axor_backend.deps import StoreDep

router = APIRouter(prefix="/v1/vault", tags=["vault"])


def _gate(request: Request, which: str) -> None:
    """Per-subsystem bearer check. A configured token is required exactly for
    its own subsystem; in the open dev posture (no token configured) the
    subsystem follows the app's global posture — which startup logs loudly."""
    expected = getattr(request.app.state.config, f"vault_{which}_token")
    if expected is None:
        return
    if request.headers.get(f"x-vault-{which}-token", "") != expected:
        raise HTTPException(403, f"vault {which}: missing or wrong token")


# ── credentials: dispense side ────────────────────────────────────────────────

@router.post("/creds/enroll")
async def vault_enroll(body: dict, request: Request, store: StoreDep) -> dict:
    _gate(request, "creds")
    from axor_backend.vault_creds import ToolCredentialVault

    return await ToolCredentialVault(store).enroll(
        str(body.get("tool", "")),
        str(body.get("endpoint", "")),
        str(body.get("secret", "")),
        list(body.get("scope_nodes", [])),
    )


@router.post("/creds/dispense")
async def vault_dispense(body: dict, request: Request, store: StoreDep) -> dict:
    _gate(request, "creds")
    from axor_backend.vault_creds import DispenseDenied, ToolCredentialVault

    try:
        return await ToolCredentialVault(store).dispense(
            str(body.get("node_id", "")),
            str(body.get("tool", "")),
            str(body.get("endpoint", "")),
        )
    except DispenseDenied as exc:
        raise HTTPException(403, exc.reason) from exc


@router.post("/creds/rotate")
async def vault_rotate(body: dict, request: Request, store: StoreDep) -> dict:
    _gate(request, "creds")
    from axor_backend.vault_creds import DispenseDenied, ToolCredentialVault

    try:
        return await ToolCredentialVault(store).rotate(
            str(body.get("tool", "")),
            str(body.get("endpoint", "")),
            str(body.get("secret", "")),
        )
    except DispenseDenied as exc:
        raise HTTPException(404, exc.reason) from exc


@router.post("/creds/revoke")
async def vault_revoke(body: dict, request: Request, store: StoreDep) -> dict:
    """Narrowing only: revoke is available over the plane in an incident;
    granting is enrollment config, never a command."""
    _gate(request, "creds")
    from axor_backend.vault_creds import DispenseDenied, ToolCredentialVault

    try:
        return await ToolCredentialVault(store).revoke(
            str(body.get("tool", "")), str(body.get("endpoint", "")),
        )
    except DispenseDenied as exc:
        raise HTTPException(404, exc.reason) from exc


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

    try:
        return await SigningCustody(store).sign(
            str(body.get("operator", "")),
            str(body.get("key_id", "")),
            base64.b64decode(str(body.get("payload_b64", ""))),
        )
    except SignRefused as exc:
        raise HTTPException(403, exc.reason) from exc


@router.get("/signing/audit")
async def vault_audit(request: Request, store: StoreDep) -> list[dict]:
    _gate(request, "signing")
    from axor_backend.vault_signing import SigningCustody

    return await SigningCustody(store).audit()
