"""EvidenceCase export and sharing (spec §8.3).

A share link publishes ONE case behind its own unauthenticated read token —
deliberately the narrowest publishable unit. Minting one is an operator
decision (`operate`), not part of uploading a trace, so the proxy's ingest key
never doubles as a publishing credential.

The resolve route is the only open write-adjacent surface in the backend, and
it is open by design: the recipient has a token, not an account. Because the
auth middleware never stamps a tenant on an open route, the LINK carries the org
it was minted under and this module adopts it — otherwise every link an identity
user created would 404 under the public tenant.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import HTMLResponse

from axor_backend.clock import now
from axor_backend.deps import SharesDep, StoreDep
from axor_backend.share import evidence_receipt_html, evidence_receipt_pdf
from axor_backend.storage import Store
from axor_backend.tenancy import current_org_id, set_current_org

router = APIRouter(prefix="/v1", tags=["share"])


async def _case_run(store: Store, run_id: str, case_index: int) -> dict:
    # One row, one query. This used to build a dict from `list_runs()` and index
    # into it — reading every run in the tenant, with every EvidenceCase blob,
    # to find one. `GET /v1/share/{token}` is unauthenticated and did that.
    run = await store.get_run(run_id)
    if run is None or case_index >= len(run["evidence"]):
        raise HTTPException(404, "no such case")
    return run


@router.post("/runs/{run_id}/cases/{case_index}/share")
async def create_share(
    run_id: str, case_index: int, store: StoreDep, shares: SharesDep
) -> dict:
    await _case_run(store, run_id, case_index)  # 404 before minting a dead token
    # The link remembers WHICH tenant's case it points at, because the route
    # that resolves it has no principal to ask.
    link = shares.create(run_id, case_index, org=current_org_id())
    await store.create_share_link(link.token, run_id, case_index, now())
    return {"token": link.token, "url": f"/v1/share/{link.token}"}


@router.delete("/share/{token}")
async def revoke_share(token: str, store: StoreDep, shares: SharesDep) -> dict:
    # Knowing a token must not let another tenant burn it: the in-memory
    # registry is process-wide, so the org check is what scopes this.
    link = shares.resolve(token)
    if link is None or link.org != current_org_id():
        raise HTTPException(404, "unknown token")
    shares.revoke(token)
    await store.revoke_share_link(token)
    return {"revoked": token}


@router.get("/share/{token}")
async def resolve_share(
    token: str, store: StoreDep, shares: SharesDep
) -> HTMLResponse:
    link = shares.resolve(token)
    if link is None:
        raise HTTPException(404, "link revoked or unknown")
    set_current_org(link.org)  # this route is open; adopt the link's tenant
    run = await _case_run(store, link.run_id, link.case_index)
    return HTMLResponse(evidence_receipt_html(
        link.run_id, run["evidence"][link.case_index], run.get("scenario", "")
    ))


@router.get("/runs/{run_id}/cases/{case_index}/export")
async def export_case(
    run_id: str, case_index: int, store: StoreDep, format: str = "html"  # noqa: A002
) -> Response:
    run = await _case_run(store, run_id, case_index)
    case = run["evidence"][case_index]
    scenario = run.get("scenario", "")
    if format == "pdf":
        return Response(
            content=evidence_receipt_pdf(run_id, case, scenario),
            media_type="application/pdf",
            headers={
                "Content-Disposition":
                    f'attachment; filename="evidence-{run_id}-{case_index}.pdf"'
            },
        )
    return HTMLResponse(evidence_receipt_html(run_id, case, scenario))
