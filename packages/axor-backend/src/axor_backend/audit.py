"""The workspace's audit trail of controls: who paused, stopped or capped which
agent, who pulled the team's brake, who granted an exception, changed the team's
rules, the seat limit, a skill, a memory item — and when.

One append-only row per act, in the custody-log table the key and signing logs
already use (``vault_audit``, kind ``control``): evicted by age under the
deployment's retention window, never by count, so the people it records cannot
flush it by acting. Rows are written after the act succeeded; a refused request
is not an act. Never a secret, never a note's text beyond its first words — the
trail says what was done, not what was said.
"""
from __future__ import annotations

from typing import Any

from fastapi import Request

from axor_backend.clock import now

KIND = "control"
KEY_KIND = "api_key"          # routers/keys.py — the credential acts, shown alongside
RETENTION_SETTING = "audit_retention_days"
MIN_RETENTION_DAYS = 30

# a node command's state keys, said as the act
_DELTA_ACTS = (
    ("budget_cap_calls", "budget"),
    ("pending_excision", "excise"),
    ("pending_injection", "inject"),
    ("replan", "replan"),
    ("pending_experiment", "experiment"),
)


def actor(request: Request) -> dict[str, Any]:
    """Who acted: a signed-in person by email, a key by its id, the master
    token as the operator."""
    p = getattr(request.state, "principal", None)
    if p is None:
        return {"kind": "open", "who": "operator"}
    who = p.email or p.key_id or p.kind
    return {"kind": p.kind, "who": str(who), **({"role": p.role} if p.role else {})}


def command_acts(delta: dict) -> list[tuple[str, dict[str, Any]]]:
    """A node command's delta as the acts it is (one command can be several)."""
    acts: list[tuple[str, dict[str, Any]]] = []
    if delta.get("stopped"):
        acts.append(("cascade_stop" if delta.get("cascade") else "stop", {}))
    elif "stopped" in delta:
        # "Start it again": a stopped agent's next session may run
        acts.append(("start_again", {}))
    elif "paused" in delta:
        acts.append(("pause" if delta["paused"] else "resume", {}))
    for key, act in _DELTA_ACTS:
        if key in delta:
            value = delta[key]
            detail: dict[str, Any] = {}
            if act == "budget":
                detail["calls"] = value
            elif isinstance(value, dict) and value.get("reason"):
                detail["reason"] = str(value["reason"])[:200]
            acts.append((act, detail))
    return acts


async def record(store: Any, request: Request | None, action: str, target: str = "",  # noqa: ANN401
                 *, who: str | None = None, as_role: str | None = None,
                 **detail: Any) -> None:  # noqa: ANN401
    """One act. ``who`` names the actor where the request does not (a pair
    guest, by the link they were given); ``as_role`` is then their role —
    not the role of the service that told us."""
    by = actor(request) if request is not None else {"kind": "open", "who": "operator"}
    if who:
        by = {k: v for k, v in {**by, "who": who}.items() if k != "role"}
        if as_role:
            by["role"] = as_role
    entry = {"ts": now(), "action": action, "by": by, "target": target,
             **({"detail": detail} if detail else {})}
    await store.add_vault_audit(KIND, entry, entry["ts"])


# Acts on the workspace's people, sign-in and sessions. The login service
# (axor-identity) keeps the people; it tells this trail what changed, with a
# short-lived token only it can sign (user ``svc_identity``).
IDENTITY_SERVICE = "svc_identity"
IDENTITY_ACTS = frozenset({
    "member_added", "member_role", "member_removed", "invite_sent", "invite_withdrawn",
    "sso_set", "sso_removed", "scim_token_made", "scim_token_revoked", "scim_group_role",
    "scim_added", "scim_removed", "scim_role", "scim_renamed", "sessions_limit",
    "sessions_ended",
    "plan_activated", "plan_renewed", "plan_upgraded",
    # a move down the ladder, once identity follows it (QA-306, QA-305)
    "plan_downgraded",
})
IDENTITY_DETAIL = frozenset({"role", "to", "from", "protocol", "domains", "enforce",
                             "default_role", "hours", "ended", "group", "tier", "until",
                             "invited", "by_group", "via"})


def first_words(text: str, n: int = 60) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"
