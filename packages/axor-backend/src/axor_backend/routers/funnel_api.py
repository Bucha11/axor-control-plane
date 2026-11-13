"""The sign-up funnel (funnel.py): the browser reports steps, the operator reads them."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, Response

from axor_backend import funnel

router = APIRouter(prefix="/v1", tags=["funnel"])


@router.post("/funnel", status_code=204)
async def record(request: Request, body: dict) -> Response:
    """One step, anonymously. Open (a visitor has no credential yet) and
    rate-limited; anything off the whitelist is refused."""
    limit = getattr(request.app.state, "funnel_limit", None)
    if limit is None:
        limit = request.app.state.funnel_limit = funnel.RateLimit()
    forwarded = request.headers.get("x-forwarded-for", "")
    address = forwarded.split(",")[0].strip() or (request.client.host if request.client else "")
    if not limit.allow(address):
        raise HTTPException(429, "too many funnel events from this address")
    event, session_id = body.get("event"), body.get("session_id")
    detail = body.get("detail", "")
    if not funnel.valid(event, session_id, detail):
        raise HTTPException(400, "unknown funnel event")
    await request.app.state.store.add_funnel_event(
        datetime.now(UTC).isoformat(), str(session_id), str(event), str(detail))
    return Response(status_code=204)


@router.get("/funnel")
async def report(request: Request, days: int = 30) -> dict:
    """Distinct tabs reaching each step over the window, conversion from the
    step before and from a visit, which features prompted sign-ups, and how
    many real agents got connected. The operator's — not a tenant's."""
    config = request.app.state.config
    principal = getattr(request.state, "principal", None)
    if config.auth_enabled and (principal is None or principal.kind != "master"):
        raise HTTPException(403, "the funnel is the operator's (master token)")
    days = max(1, min(int(days), 365))
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    counts = await request.app.state.store.funnel_counts(since)
    sessions = counts["sessions"]
    steps, previous, first = [], None, None
    for label, events in funnel.FUNNEL:
        reached: set[str] = set()
        for e in events:
            reached |= sessions.get(e, set())
        n = len(reached)
        first = n if first is None else first
        steps.append({
            "step": label, "count": n,
            "from_previous": round(n / previous, 3) if previous else None,
            "from_visit": round(n / first, 3) if first else None,
        })
        previous = n
    return {
        "days": days,
        "steps": steps,
        "signup_prompts": counts["prompts"],
        "agents_connected": counts["events"].get("agent_connected", 0),
    }
