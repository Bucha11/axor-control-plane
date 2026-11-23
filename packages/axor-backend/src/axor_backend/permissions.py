"""Who may do what to the team's agents, beyond their own.

Roles come from the identity service — owner, admin, member, viewer — and say
little about agents: a team lead who is on call needs to stop a runaway session
of a teammate without being made an admin of the workspace, and a team that
wants only its leads to turn skills off for everyone needs to say so. A
workspace's *permissions* answer that, one capability at a time:

- granted to members as a whole, or not;
- granted to named people (by email) whatever their role.

Owners and admins always may (they set this — they cannot lock themselves
out); viewers never may (they watch). A credential that is not a person — the
master token, an operator key — is governed by its scopes, as before: these
are about people.

The defaults are what the plane did before this existed, so nothing changes
until an admin changes it.
"""
from __future__ import annotations

from typing import Any

SETTING = "permissions"

# capability -> (what it lets a person do, members may by default)
CAPABILITIES: dict[str, tuple[str, bool]] = {
    "stop_others": ("pause, stop and cap a teammate's coding session or tree", False),
    "brake": ("hold every coding agent of the team at once (the team's brake)", False),
    "exceptions": ("let one refused thing through for the team, or trust a source", False),
    "skills": ("turn a skill off for the team, or share one with it", True),
    "memory": ("decide what goes into the team's memory", True),
    "production": ("pause, stop and steer production agents", True),
}


def _is_person(principal: Any) -> bool:  # noqa: ANN401
    return getattr(principal, "kind", None) == "user" and bool(getattr(principal, "email", ""))


async def held(store: Any) -> dict[str, dict[str, Any]]:  # noqa: ANN401
    """Every capability as the workspace set it, the defaults filled in."""
    raw = await store.get_setting(SETTING)
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for cap, (_, members) in CAPABILITIES.items():
        got = raw.get(cap) if isinstance(raw.get(cap), dict) else {}
        out[cap] = {
            "members": bool(got.get("members", members)),
            "people": sorted({str(p).strip().lower() for p in got.get("people") or []
                              if "@" in str(p)}),
        }
    return out


def allows(grants: dict[str, dict[str, Any]], principal: Any, cap: str) -> bool:  # noqa: ANN401
    if not _is_person(principal):
        return True                     # a key or the master token: its scopes decide
    role = getattr(principal, "role", None)
    if role in ("owner", "admin"):
        return True
    if role == "viewer":
        return False
    g = grants.get(cap) or {}
    if str(principal.email).lower() in g.get("people", []):
        return True
    return role == "member" and bool(g.get("members"))


async def may(store: Any, principal: Any, cap: str) -> bool:  # noqa: ANN401
    return allows(await held(store), principal, cap)


def mine(grants: dict[str, dict[str, Any]], principal: Any) -> dict[str, bool]:  # noqa: ANN401
    return {cap: allows(grants, principal, cap) for cap in CAPABILITIES}


def require_admin(principal: Any) -> None:  # noqa: ANN401
    """A workspace's owner or admin (a person), or a credential holding the
    admin scope. The scope alone is the owner's: an admin of the workspace
    answers for its agents too, so what is theirs to decide is checked here."""
    from fastapi import HTTPException

    if _is_person(principal):
        if getattr(principal, "role", None) in ("owner", "admin"):
            return
        raise HTTPException(403, "an owner or admin of the workspace decides this")
    if principal is not None and "admin" not in getattr(principal, "scopes", frozenset()):
        raise HTTPException(403, "this takes the admin scope")


def on_business(request: Any) -> bool:  # noqa: ANN401
    """Whether this workspace's plan is Business or above — single sign-on's,
    the audit trail's export and who-may's rung (team and up include it)."""
    from axor_backend.licensing import active_license
    from axor_backend.tenancy import current_org_id

    lic = active_license(request.app.state, current_org_id())
    return lic is not None and lic.tier_at_least("business")


def require_business(request: Any, what: str) -> None:  # noqa: ANN401
    from fastapi import HTTPException

    if not on_business(request):
        raise HTTPException(402, {"error": "plan_required", "plan": "business",
                                  "detail": f"{what} is on the Business plan "
                                            f"($40 per developer) — see Pricing"})
