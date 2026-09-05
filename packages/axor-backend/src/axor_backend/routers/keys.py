"""Auth status and scoped API keys (architecture §9).

Minting is the operator's act of delegating a subset of their own authority:
a key lists exactly the scopes it may use, and optionally the ONE node it may
speak as. Listing keys is credential enumeration, so reading this surface needs
`admin` too (auth._READ_POLICY) — a policy entry that guards only the write
would leave the inventory open.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from axor_backend import auth as auth_mod
from axor_backend.auth import hash_secret
from axor_backend.clock import now
from axor_backend.deps import ConfigDep, StoreDep
from axor_backend.security import resolve_principal

router = APIRouter(prefix="/v1", tags=["auth"])


@router.get("/auth/status")
async def auth_status(request: Request, config: ConfigDep) -> dict:
    """Whether auth is on, and (if a token was sent) whether it's valid + its
    scopes. Open so the UI can decide whether to prompt for a token."""
    if not config.auth_enabled:
        return {
            "auth_enabled": False,
            "authenticated": True,
            "scopes": sorted(auth_mod.SCOPES),
        }
    principal = await resolve_principal(request)
    return {
        "auth_enabled": True,
        "authenticated": principal is not None,
        "scopes": sorted(principal.scopes) if principal else [],
    }


@router.post("/keys", status_code=201)
async def create_key(body: dict, store: StoreDep) -> dict:
    scopes = body.get("scopes", ["read"])
    if not scopes:
        raise HTTPException(400, "scopes must be non-empty")
    bad = set(scopes) - auth_mod.SCOPES
    if bad:
        raise HTTPException(400, f"unknown scopes: {sorted(bad)}")
    # Optional node binding: a key minted for one governed node may only post
    # telemetry / facts / health AS that node (auth.plane_node_of). Omit it for
    # a fleet-wide operator key — the pre-existing shape.
    node_id = body.get("node_id")
    if node_id is not None and (not isinstance(node_id, str) or not node_id):
        raise HTTPException(400, "node_id must be a non-empty string when given")
    key_id, secret = auth_mod.generate_key()
    await store.create_api_key(
        key_id, hash_secret(secret), scopes, body.get("label", ""), now(),
        node_id=node_id,
    )
    # The full secret is returned exactly once; only its hash is stored.
    return {"key_id": key_id, "secret": secret, "scopes": scopes, "node_id": node_id}


@router.get("/keys")
async def list_keys(store: StoreDep) -> list[dict]:
    return await store.list_api_keys()


@router.delete("/keys/{key_id}")
async def delete_key(key_id: str, store: StoreDep) -> dict:
    if not await store.delete_api_key(key_id):
        raise HTTPException(404, "unknown key")
    return {"revoked": key_id}
