"""Auth status and scoped API keys (architecture §9).

Minting is the operator's act of delegating a subset of their own authority:
a key lists exactly the scopes it may use, and optionally the ONE node it may
speak as. Listing keys is credential enumeration, so reading this surface needs
`admin` too (auth._READ_POLICY) — a policy entry that guards only the write
would leave the inventory open.

"A subset of their own authority" is enforced here, by `_delegable`, and was
not: the route validated the requested scopes against the whole ladder rather
than against the caller's. Because the ladder does not imply (`Principal.may`
is exact membership), a key holding ONLY `admin` is refused `GET /v1/runs` and
refused the plane — and could mint itself a key holding all four scopes and no
node binding, then use it. Both walls this module puts up, scopes and
`may_speak_for`, were reachable through the door that raises them.

Every mint and every revoke is appended to the custody log (`AUDIT_KIND`) for
the same reason the vault's dispensing is: the whole episode above used to
leave no trace at all — not in the key listing, which only shows keys that
still exist, and not in any row of any table. A credential that can be issued,
used and withdrawn with no record is not a credential an operator can reason
about after the fact.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from axor_backend import auth as auth_mod
from axor_backend.auth import Principal, hash_secret
from axor_backend.clock import now
from axor_backend.deps import ConfigDep, StoreDep
from axor_backend.security import log as auth_log
from axor_backend.security import resolve_principal

router = APIRouter(prefix="/v1", tags=["auth"])

# The custody log this router writes to (storage.VAULT_AUDIT_KINDS), so the
# retention sweep bounds it like the vault's two.
AUDIT_KIND = "api_key"

# A label is a human note in the key listing, not a payload. Bounded because
# nothing bounded it: a 100 000-character label was stored with a 201.
MAX_LABEL = 200

AUDIT_PAGE = 200
AUDIT_PAGE_MAX = 1000


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
    if principal is None and _presented_a_token(request):
        # This route is open, so `auth_middleware` returns before it can log
        # anything — and this is the one route that tells an anonymous caller
        # whether a token is good. 500 guesses here used to produce 500 × 200
        # and not one line anywhere, while the same 500 guesses at any other
        # route produced 500 × 401 and 500 warnings. A refusal is a refusal
        # wherever it happens.
        auth_log.warning(
            "401-equivalent GET /v1/auth/status: a token was presented and did "
            "not resolve",
        )
    return {
        "auth_enabled": True,
        "authenticated": principal is not None,
        "scopes": sorted(principal.scopes) if principal else [],
    }


def _presented_a_token(request: Request) -> bool:
    """Whether the caller sent something they meant as a credential. Silence is
    the UI asking whether to prompt; a bad token is a failed attempt."""
    header = request.headers.get("authorization", "")
    return bool(header.strip()) or bool(request.query_params.get("token"))


def _scopes_of(body: dict) -> list[str]:
    scopes = body.get("scopes", ["read"])
    if not isinstance(scopes, list) or not all(isinstance(s, str) for s in scopes):
        # `set(scopes)` on anything else either accepted the wrong thing or
        # crashed: a dict minted a real admin key (its KEYS passed the check),
        # a nested list raised TypeError out of the route as 500 {"error":
        # "internal"} — the caller's mistake reported as ours.
        raise HTTPException(400, "scopes must be a list of strings")
    if not scopes:
        raise HTTPException(400, "scopes must be non-empty")
    bad = set(scopes) - auth_mod.SCOPES
    if bad:
        raise HTTPException(400, f"unknown scopes: {sorted(bad)}")
    return scopes


def _delegable(caller: Principal | None, scopes: list[str], node_id: str | None,
               ) -> str | None:
    """The node binding the minted key must carry, or raise.

    A caller may hand out only what it holds. `None` (auth disabled) delegates
    freely — there is no authority to be a subset of. The master token and a
    human `owner` hold the whole ladder and no node, so the operator's own
    minting is unchanged; what this stops is a key widening itself.
    """
    if caller is None:
        return node_id
    over = sorted(set(scopes) - set(caller.scopes))
    if over:
        raise HTTPException(
            403,
            f"{caller.kind} {caller.key_id} holds {sorted(caller.scopes)} and "
            f"may not delegate {over}: a key is a subset of the authority that "
            f"mints it",
        )
    if caller.node_id is None:
        return node_id
    # A node-bound key mints only for its own node — otherwise `may_speak_for`
    # is one POST away from being optional.
    if node_id is not None and node_id != caller.node_id:
        raise HTTPException(
            403,
            f"key {caller.key_id} is bound to node {caller.node_id!r} and may "
            f"not mint a key for {node_id!r}",
        )
    return caller.node_id


async def _audit(store: object, action: str, request: Request, **fields: object,
                 ) -> None:
    """One row per credential-lifecycle act. Never the secret, never its hash."""
    caller = getattr(request.state, "principal", None)
    entry = {
        "action": action,
        "ts": now(),
        "by": {
            "kind": caller.kind if caller else "unauthenticated",
            "id": caller.key_id if caller else None,
        },
        **fields,
    }
    await store.add_vault_audit(AUDIT_KIND, entry, entry["ts"])


@router.post("/keys", status_code=201)
async def create_key(body: dict, request: Request, store: StoreDep) -> dict:
    scopes = _scopes_of(body)
    # Optional node binding: a key minted for one governed node may only post
    # telemetry / facts / health AS that node (auth.plane_node_of). Omit it for
    # a fleet-wide operator key — the pre-existing shape.
    node_id = body.get("node_id")
    if node_id is not None and (not isinstance(node_id, str) or not node_id):
        raise HTTPException(400, "node_id must be a non-empty string when given")
    label = body.get("label", "")
    if not isinstance(label, str):
        # It went to the INSERT as-is and came back a ProgrammingError → 500.
        raise HTTPException(400, "label must be a string")
    if len(label) > MAX_LABEL:
        raise HTTPException(400, f"label must be at most {MAX_LABEL} characters")
    node_id = _delegable(getattr(request.state, "principal", None), scopes, node_id)
    key_id, secret = auth_mod.generate_key()
    await store.create_api_key(
        key_id, hash_secret(secret), scopes, label, now(), node_id=node_id,
    )
    await _audit(store, "mint", request, key_id=key_id, scopes=sorted(scopes),
                 node_id=node_id, label=label)
    # The full secret is returned exactly once; only its hash is stored.
    return {"key_id": key_id, "secret": secret, "scopes": scopes, "node_id": node_id}


@router.get("/keys")
async def list_keys(store: StoreDep) -> list[dict]:
    return await store.list_api_keys()


@router.get("/keys/audit")
async def keys_audit(
    store: StoreDep, limit: int = AUDIT_PAGE, before_id: int | None = None,
) -> list[dict]:
    """Every mint and revoke, newest first, paged by `audit_id`.

    The key listing answers "what exists now"; this answers "what was issued",
    which is the question that survives a revoke.
    """
    return await store.vault_audit_entries(
        AUDIT_KIND, limit=max(1, min(limit, AUDIT_PAGE_MAX)), before_id=before_id,
    )


@router.delete("/keys/{key_id}")
async def delete_key(key_id: str, request: Request, store: StoreDep) -> dict:
    if not await store.delete_api_key(key_id):
        raise HTTPException(404, "unknown key")
    await _audit(store, "revoke", request, key_id=key_id)
    return {"revoked": key_id}
