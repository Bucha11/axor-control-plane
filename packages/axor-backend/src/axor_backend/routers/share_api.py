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
from axor_backend.deps import StoreDep
from axor_backend.share import evidence_receipt_html, evidence_receipt_pdf, new_token
from axor_backend.storage import Store
from axor_backend.tenancy import set_current_org

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
    run_id: str, case_index: int, store: StoreDep
) -> dict:
    await _case_run(store, run_id, case_index)  # 404 before minting a dead token
    # The row remembers WHICH tenant's case it points at, because the route
    # that resolves it has no principal to ask.
    token = new_token()
    await store.create_share_link(token, run_id, case_index, now())
    return {"token": token, "url": f"/v1/share/{token}"}


@router.delete("/share/{token}")
async def revoke_share(token: str, store: StoreDep) -> dict:
    # Knowing a token must not let another tenant burn it — the UPDATE is
    # org-scoped, so a miss is either "no such token" or "not yours" and both
    # answer the same 404.
    if not await store.revoke_share_link(token):
        raise HTTPException(404, "unknown token")
    return {"revoked": token}


@router.get("/share/{token}")
async def resolve_share(token: str, store: StoreDep) -> HTMLResponse:
    """Resolve straight from the store — there is no in-process index.

    There used to be one, rehydrated at boot, and it was authoritative for this
    route while the table was authoritative for everything else. Retention
    deletes a pruned run's links from the table; the index kept them, so the
    moment a run id was used again (ids are client-chosen, and `upsert_run`
    exists to reuse them) an old public token served the NEW case. The row is
    the only source of truth now, which also means a revoke lands immediately
    rather than at the next restart.
    """
    link = await store.get_share_link(token)
    if link is None or link["revoked"]:
        raise HTTPException(404, "link revoked or unknown")
    set_current_org(link["org_id"])  # this route is open; adopt the link's tenant
    run = await _case_run(store, link["run_id"], link["case_index"])
    return HTMLResponse(evidence_receipt_html(
        link["run_id"], run["evidence"][link["case_index"]],
        run.get("scenario", ""),
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
