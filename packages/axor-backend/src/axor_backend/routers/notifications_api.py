"""Webhook subscriptions and the log of deliveries that never arrived.

This is the loud half of quiet-until-wrong: the UI stays calm, the webhook fires
when something is actually wrong. A failed delivery lands in the dead-letter log
rather than vanishing, which is why that log is read through from the database
and not from process memory.

Free/paid line: one plain webhook catching everything is free forever. Routing —
channel labels and node globs — is the org layer.
"""
from __future__ import annotations

import math

from fastapi import APIRouter, HTTPException

from axor_backend.deps import NotifierDep, StateDep, StoreDep
from axor_backend.licensing import require_ee
from axor_backend.limits import MAX_DEBOUNCE_SECONDS
from axor_backend.tenancy import current_org_id

router = APIRouter(prefix="/v1/notifications", tags=["notifications"])


def _text(body: dict, field: str, *, required: bool = False) -> str:
    """One string field, or a 400 naming it.

    `str(body.get(field) or "")` was the old shape and it does not refuse
    anything: a dict label became its own repr, and a non-string `url` reached
    `check_webhook_url` and left the route as `AttributeError: 'dict' object has
    no attribute 'decode'` — a 500 for a body the caller got wrong.
    """
    value = body.get(field)
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value):
        raise HTTPException(400, f"{field} must be a non-empty string")
    return value


def _triggers(body: dict) -> list[str]:
    """The trigger names, or a 400. `notifier.validate` checks them against
    `TRIGGERS`; this checks that they are names at all.

    `set(triggers)` accepted whatever it could hash: a dict subscribed to its
    KEYS and answered 200, a bare string subscribed to its letters and answered
    400 listing them, and a nested list left the route as `TypeError:
    unhashable type: 'list'`.
    """
    triggers = body.get("triggers", [])
    if not isinstance(triggers, list) or not all(
        isinstance(t, str) for t in triggers
    ):
        raise HTTPException(400, "triggers must be a list of trigger names")
    if not triggers:
        raise HTTPException(400, "triggers must be non-empty")
    return triggers


def _debounce(body: dict) -> float:
    """Seconds, finite and within the bound, or a 400.

    `float(...)` accepted everything `json.loads` produces, and `json.loads`
    accepts the `Infinity` and `NaN` literals. An infinite debounce answered 200
    and then fired once, ever — `due()` reads `(now - last) < inf` as true for
    every later event, which is the permanent mute this system was already fixed
    for once. `NaN` reached the INSERT and SQLite refused it as a NOT NULL
    violation, so the caller's number came back as our 500.
    """
    raw = body.get("debounce_seconds", 0.0)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        # `true` is a number in Python and silently became a one-second
        # debounce; a string is not a debounce at all.
        raise HTTPException(400, "debounce_seconds must be a number")
    seconds = float(raw)
    if math.isnan(seconds) or math.isinf(seconds):
        raise HTTPException(400, "debounce_seconds must be a finite number")
    if seconds < 0:
        raise HTTPException(400, "debounce_seconds must not be negative")
    if seconds > MAX_DEBOUNCE_SECONDS:
        raise HTTPException(
            400,
            f"debounce_seconds must be at most {MAX_DEBOUNCE_SECONDS} "
            f"(AXOR_MAX_DEBOUNCE_SECONDS): past that a subscription is not "
            f"debounced, it is muted, and a live row that delivers nothing is "
            f"what unsubscribing exists to avoid",
        )
    return seconds


@router.post("/subscribe")
async def notif_subscribe(
    body: dict, state: StateDep, store: StoreDep, notifier: NotifierDep
) -> dict:
    from axor_backend.notifications import WebhookRefused

    url = _text(body, "url", required=True)
    triggers = _triggers(body)
    debounce = _debounce(body)
    label = _text(body, "label")
    node_pattern = _text(body, "node_pattern") or "*"
    if label or node_pattern != "*":
        require_ee(
            state, current_org_id(),
            "notification routing (channels / node patterns)",
        )
    # Persist BEFORE registering in memory. The other order let a subscription
    # start firing and then fail to be written, so it delivered until the next
    # restart and then silently stopped — the one failure mode a notification
    # system must not have. The store call is idempotent on
    # url+triggers+pattern, so the boot rehydrate never double-registers.
    try:
        notifier.validate(url, triggers)
    except (WebhookRefused, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    await store.add_subscription(
        url, triggers, debounce, label=label, node_pattern=node_pattern
    )
    notifier.subscribe(
        url, triggers, debounce, label=label, node_pattern=node_pattern,
        org=current_org_id(),
    )
    return {
        "subscribed": url, "triggers": triggers,
        "label": label, "node_pattern": node_pattern,
    }


@router.post("/unsubscribe")
async def notif_unsubscribe(
    body: dict, store: StoreDep, notifier: NotifierDep
) -> dict:
    """Stop delivering to a webhook. There was no way to do this at all.

    A registered webhook fired forever, and a wrong or leaked URL could only be
    removed by editing the database — while the body it receives carries node
    ids, levels, a permalink, and for `license_expiring` the licensed
    organization.

    Removed from the store FIRST, for the mirror image of the reason subscribe
    persists first: the other order would stop delivery in this process and
    leave a row that resurrects the webhook at the next restart.
    """
    url = _text(body, "url", required=True)
    removed = await store.remove_subscriptions(url)
    notifier.unsubscribe(url, org=current_org_id())
    if not removed:
        raise HTTPException(404, f"no subscription for {url!r} in this tenant")
    return {"unsubscribed": url, "removed": removed}


@router.get("/subscriptions")
async def notif_subscriptions(store: StoreDep) -> list[dict]:
    return await store.list_subscriptions()


@router.get("/dead-letters")
async def notif_dead_letters(store: StoreDep) -> list[dict]:
    # Read-through from the store: dead letters persist across restarts
    # (migration 0002) — the log of lost deliveries must not itself be lossy.
    rows = await store.list_dead_letters()
    return [
        {"url": d["url"], "error": d["error"], "attempts": d["attempts"],
         "trigger": (d["payload"] or {}).get("trigger"),
         "created_ts": d["created_ts"]}
        for d in rows
    ]
