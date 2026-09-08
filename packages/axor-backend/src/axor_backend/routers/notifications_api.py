"""Webhook subscriptions and the log of deliveries that never arrived.

This is the loud half of quiet-until-wrong: the UI stays calm, the webhook fires
when something is actually wrong. A failed delivery lands in the dead-letter log
rather than vanishing, which is why that log is read through from the database
and not from process memory.

Free/paid line: one plain webhook catching everything is free forever. Routing —
channel labels and node globs — is the org layer.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from axor_backend.deps import NotifierDep, StateDep, StoreDep
from axor_backend.licensing import require_ee
from axor_backend.tenancy import current_org_id

router = APIRouter(prefix="/v1/notifications", tags=["notifications"])


@router.post("/subscribe")
async def notif_subscribe(
    body: dict, state: StateDep, store: StoreDep, notifier: NotifierDep
) -> dict:
    from axor_backend.notifications import WebhookRefused

    url = body.get("url")
    triggers = body.get("triggers", [])
    if not url or not triggers:
        raise HTTPException(400, "url and triggers required")
    try:
        debounce = float(body.get("debounce_seconds", 0.0))
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "debounce_seconds must be a number") from exc
    label = str(body.get("label") or "")
    node_pattern = str(body.get("node_pattern") or "*")
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
    url = str(body.get("url") or "")
    if not url:
        raise HTTPException(400, "url required")
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
