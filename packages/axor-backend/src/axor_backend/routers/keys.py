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


@router.get("/security/posture")
async def security_posture(config: ConfigDep) -> dict:
    """What this deployment's walls are, item by item, with how to raise the
    ones that are down — the same facts the boot log warns about
    (lifecycle.warn_about_open_posture), where an operator looks. Config only:
    no secret, no key material."""
    signing = ("signed" if config.operator_keys and not config.allow_unsigned
               else "unsigned_accepted" if config.allow_unsigned else "signed_no_keys")
    items = [
        {"id": "auth", "ok": config.auth_enabled,
         "what": "every request needs a token or a sign-in",
         "fix": "set AXOR_API_TOKEN on the backend"},
        {"id": "command_signing", "ok": signing == "signed",
         "what": "commands to agents (pause, stop, cap) are signed by an operator key",
         "fix": "set AXOR_OPERATOR_KEYS and turn AXOR_ALLOW_UNSIGNED off"
                if signing == "unsigned_accepted" else "set AXOR_OPERATOR_KEYS"},
        {"id": "vault_creds_token", "ok": config.vault_creds_token is not None,
         "what": "dispensing tool credentials needs its own token, not just admin",
         "fix": "set AXOR_VAULT_CREDS_TOKEN"},
        {"id": "vault_signing_token", "ok": config.vault_signing_token is not None,
         "what": "requesting a signature needs its own token, not just admin",
         "fix": "set AXOR_VAULT_SIGNING_TOKEN"},
        # on the hosted plane each plan has its window (ee.entitlements.RETENTION_DAYS)
        {"id": "retention", "ok": config.hosted or config.retention_days is not None,
         "what": ("runs are kept as long as each workspace's plan says" if config.hosted
                  else f"runs are kept {config.retention_days:g} days" if config.retention_days
                  else "runs are kept forever"),
         "fix": "set AXOR_RETENTION_DAYS"},
    ]
    if config.vendor_pubkey:
        items.append({"id": "licensed_org", "ok": bool(config.org),
                      "what": "only a license issued to this organization activates here",
                      "fix": "set AXOR_ORG to the name on your license"})
    return {"items": items, "signing": signing}


@router.get("/auth/status")
async def auth_status(request: Request, config: ConfigDep) -> dict:
    """Whether auth is on, and (if a token was sent) whether it's valid + its
    scopes. Open so the UI can decide whether to prompt for a token."""
    if not config.auth_enabled:
        return {
            "auth_enabled": False,
            "authenticated": True,
            "scopes": sorted(auth_mod.SCOPES),
            "operator": True,
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
        # whether a visitor can start as a guest, and whether this caller is one
        "guest_sessions": getattr(request.app.state, "guests", None) is not None,
        "guest": principal is not None and principal.kind == "guest",
        # the deployment's operator (the master token): what only they may read,
        # such as the sign-up funnel, is asked for only then — not tried and
        # refused with a 403 on every Settings load (QA-042)
        "operator": principal is not None and principal.kind == "master",
    }


@router.post("/auth/guest", status_code=201)
async def guest_session(request: Request) -> dict:
    """A guest session for a hosted visitor: work now, sign up to keep it.
    404 unless the deployment turned guests on (AXOR_GUEST_SESSIONS)."""
    from axor_backend.guests import GuestLimit

    guests = getattr(request.app.state, "guests", None)
    if guests is None:
        raise HTTPException(404, "guest sessions are off on this deployment")
    forwarded = request.headers.get("x-forwarded-for", "")
    address = forwarded.split(",")[0].strip() or (request.client.host if request.client else "")
    try:
        return await guests.issue(address)
    except GuestLimit as exc:
        raise HTTPException(429, str(exc),
                            headers={"Retry-After": str(exc.retry_after)}) from exc


@router.post("/auth/claim-guest")
async def claim_guest(request: Request, body: dict) -> dict:
    """Move a guest session's work into the signed-in account's org: runs,
    agents and their state, evidence, health checks, reputation. The account
    wins a clash. The guest token stops working."""
    from axor_backend.guests import hash_token

    principal = getattr(request.state, "principal", None)
    if principal is None or principal.kind != "user" or not principal.org:
        raise HTTPException(403, "claiming guest work needs a signed-in account")
    token = body.get("guest_token")
    if not isinstance(token, str) or not token.startswith("gs_"):
        raise HTTPException(400, "guest_token is required")
    store = request.app.state.store
    examples = await store.guest_examples(hash_token(token))
    moved = await store.claim_guest(hash_token(token), principal.org)
    # what the person did, not the examples they looked at (those move too)
    own = {t: n - examples.get(t, 0) for t, n in moved.items() if n - examples.get(t, 0) > 0}
    return {"claimed": bool(moved), "moved": own}


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
            # a person by their email, so the audit trail names them
            **({"email": caller.email} if caller and caller.email else {}),
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


# ── a member's own key ──────────────────────────────────────────────────────
#
# Minting keys is an owner's act (`/v1/keys`, scope admin), which left every
# other developer of a team unable to connect their own machine: the Connect
# page offered "create the key" and answered 403. A signed-in member may mint
# a key for themselves, labelled with their email, carrying what their role
# carries — a member's is an operator key: it reports, reads the plane and
# steers agents — never `admin`. They list and revoke the ones they minted; the
# owner sees them all and changes or revokes any.

MINE_SCOPES = ["read", "ingest", "operate"]


def _person(request: Request) -> Principal | None:
    """The signed-in person, or None when auth is off. A key may not mint
    keys for itself: a personal key is a person's."""
    caller = getattr(request.state, "principal", None)
    if caller is None:
        return None
    if caller.kind != "user" or not caller.user_id:
        raise HTTPException(403, "a personal key is minted by a signed-in person")
    return caller


@router.post("/keys/mine", status_code=201)
async def create_my_key(request: Request, store: StoreDep) -> dict:
    caller = _person(request)
    label = f"{caller.email or caller.user_id} · coding agents" if caller else "coding agents"
    # what the person's role carries, up to operator: a key is never more
    scopes = [s for s in MINE_SCOPES if caller is None or caller.may(s)]
    key_id, secret = auth_mod.generate_key()
    await store.create_api_key(
        key_id, hash_secret(secret), scopes, label[:MAX_LABEL], now(),
        created_by=caller.user_id if caller else None,
    )
    await _audit(store, "mint", request, key_id=key_id, scopes=scopes,
                 node_id=None, label=label, personal=True)
    return {"key_id": key_id, "secret": secret, "scopes": scopes, "node_id": None}


@router.get("/keys/mine")
async def list_my_keys(request: Request, store: StoreDep) -> list[dict]:
    caller = _person(request)
    if caller is None:
        return []
    return await store.list_api_keys(created_by=caller.user_id)


@router.delete("/keys/mine/{key_id}")
async def delete_my_key(key_id: str, request: Request, store: StoreDep) -> dict:
    caller = _person(request)
    if caller is None or not await store.delete_api_key(key_id, created_by=caller.user_id):
        raise HTTPException(404, "no key of yours with that id")
    await _audit(store, "revoke", request, key_id=key_id, personal=True)
    return {"revoked": key_id}


@router.delete("/keys/of/{user_id}")
async def revoke_keys_of(user_id: str, request: Request, store: StoreDep) -> dict:
    """Every key a teammate minted for themselves, revoked — when an admin
    takes them off the workspace or makes them a viewer (identity keeps the
    people, this plane the keys, so the Team page calls both). An admin's or
    the owner's act: a member operates agents, not other people's access."""
    caller = getattr(request.state, "principal", None)
    allowed = caller is None or caller.kind == auth_mod.MASTER or (
        caller.kind == "user" and caller.role in ("admin", "owner"))
    if not allowed:
        # an operate key — a member's own, say — never revokes anyone's access
        raise HTTPException(403, "an admin of this workspace takes a teammate's keys away")
    revoked = []
    for k in await store.list_api_keys(created_by=user_id):
        if await store.delete_api_key(k["key_id"], created_by=user_id):
            await _audit(store, "revoke", request, key_id=k["key_id"], personal=True,
                         reason="teammate removed or made a viewer")
            revoked.append(k["key_id"])
    return {"revoked": revoked}


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


@router.put("/keys/{key_id}")
async def set_key_scopes(key_id: str, body: dict, request: Request, store: StoreDep) -> dict:
    """Change what a key may do — its role — in place: a developer's key raised
    to read the plane too, an operator key lowered to report-only. The secret
    and the machine holding it stay the same. Within the caller's own
    authority, like minting; a node binding stays as it was."""
    scopes = _scopes_of(body)
    caller = getattr(request.state, "principal", None)
    keys = {k["key_id"]: k for k in await store.list_api_keys()}
    if key_id not in keys:
        raise HTTPException(404, "unknown key")
    _delegable(caller, scopes, keys[key_id]["node_id"])
    await store.set_api_key_scopes(key_id, scopes)
    await _audit(store, "change", request, key_id=key_id, scopes=sorted(scopes),
                 was=sorted(keys[key_id]["scopes"]))
    return {"key_id": key_id, "scopes": scopes}


@router.delete("/keys/{key_id}")
async def delete_key(key_id: str, request: Request, store: StoreDep) -> dict:
    if not await store.delete_api_key(key_id):
        raise HTTPException(404, "unknown key")
    await _audit(store, "revoke", request, key_id=key_id)
    return {"revoked": key_id}
