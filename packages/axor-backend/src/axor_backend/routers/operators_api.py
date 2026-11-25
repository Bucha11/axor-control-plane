"""Operators' browser signing keys.

- ``GET    /v1/operators``                     the workspace's keys
- ``POST   /v1/operators``                     ``{"pubkey", "label"}`` — register this
  browser's key (owner/admin; the private half never leaves the browser)
- ``PUT    /v1/operators/{id}/endorsement``    ``{"payload", "operator", "sig"}`` —
  another operator vouches for this key, so machines that trust them trust it
- ``DELETE /v1/operators/{id}``                revoke (owner/admin, or the key's own person)
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request

from axor_backend import operators
from axor_backend.deps import PrincipalDep, StoreDep
from axor_backend.signing import CommandRejected
from axor_backend.tenancy import current_org_id

router = APIRouter(prefix="/v1/operators", tags=["operators"])


def _email(principal: object) -> str:
    return str(getattr(principal, "email", "") or "")


@router.get("")
async def list_keys(store: StoreDep) -> dict:
    return {"operators": await store.operator_keys_list()}


@router.post("", status_code=201)
async def register(body: dict, request: Request, store: StoreDep,
                   principal: PrincipalDep) -> dict:
    from axor_backend import audit
    from axor_backend.permissions import require_admin

    require_admin(principal)
    email = _email(principal)
    if not email:
        raise HTTPException(403, "an operator key belongs to a signed-in person")
    pubkey = str(body.get("pubkey", "")).lower()
    try:
        ok = len(bytes.fromhex(pubkey)) == 32
    except ValueError:
        ok = False
    if not ok:
        raise HTTPException(400, "pubkey is an Ed25519 public key, 64 hex digits")
    op_id = operators.operator_id(email, pubkey)
    if await store.operator_key(op_id) is not None:
        raise HTTPException(409, "this key is registered already")
    await store.add_operator_key(op_id, email, pubkey, str(body.get("label") or ""),
                                 datetime.now(UTC).isoformat())
    await audit.record(store, request, "operator_key_added", op_id)
    return {"operator_id": op_id,
            "endorse": operators.endorsement_payload(current_org_id(), op_id, pubkey)}


@router.put("/{op_id}/endorsement")
async def endorse(op_id: str, body: dict, request: Request, store: StoreDep,
                  principal: PrincipalDep) -> dict:
    """An operator vouches for another's key: the signature is over
    ``{"kind": "operator_key", "org", "operator", "pubkey"}`` with their own
    key — a browser key of this workspace or one of the deployment's."""
    from axor_core.kernel.jcs import canonicalize

    from axor_backend import audit
    from axor_backend.permissions import require_admin

    require_admin(principal)
    key = await store.operator_key(op_id)
    if key is None or key["revoked_ts"]:
        raise HTTPException(404, "no such key, or it was revoked")
    expected = operators.endorsement_payload(current_org_id(), op_id, key["pubkey"])
    if body.get("payload") != expected:
        raise HTTPException(400, "payload is the key's endorsement document, exactly")
    endorser = body.get("operator")
    if endorser == op_id:
        raise HTTPException(400, "a key does not vouch for itself")
    try:
        await operators.verify(request.app.state, store, endorser, canonicalize(expected),
                               body.get("sig"))
    except CommandRejected as exc:
        raise HTTPException(403, f"the endorsement does not verify: {exc}") from exc
    await store.set_operator_key(op_id, endorsement={
        "payload": expected, "operator": endorser, "sig": body["sig"]})
    await audit.record(store, request, "operator_key_endorsed", op_id, by_key=endorser)
    return {"operator_id": op_id, "endorsed_by": endorser}


@router.delete("/{op_id}")
async def revoke(op_id: str, request: Request, store: StoreDep,
                 principal: PrincipalDep) -> dict:
    from axor_backend import audit, permissions

    key = await store.operator_key(op_id)
    if key is None:
        raise HTTPException(404, "no such key")
    if key["email"] != _email(principal).lower():
        permissions.require_admin(principal)
    await store.set_operator_key(op_id, revoked_ts=datetime.now(UTC).isoformat())
    await audit.record(store, request, "operator_key_revoked", op_id)
    return {"operator_id": op_id, "revoked": True}
