"""Who a machine's developer id is — the table behind "this session is Ada's".

A sidecar names its developer by an opaque id (a hash of the git address), and
a session reported on a team key carries only that. When the person's own key
reports a session carrying the id, the plane binds the id to them
(``Store.bind_developer``): first binding stands, a second person claiming the
same id disputes it and the id then names nobody. An owner or admin sees the
table, settles a dispute, binds or unbinds.

- ``GET /v1/identities``              the bindings — owner/admin
- ``PUT /v1/identities/{developer}``  ``{"email": "…"}`` binds, ``{"email": null}``
  unbinds — owner/admin
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request

from axor_backend.deps import PrincipalDep, StoreDep

router = APIRouter(tags=["identities"])


@router.get("/v1/identities")
async def list_identities(store: StoreDep, principal: PrincipalDep) -> dict:
    from axor_backend.permissions import require_admin

    require_admin(principal)
    return {"identities": await store.developer_bindings(),
            "machines": await _workers(store)}


async def _workers(store: StoreDep) -> list[dict]:
    """The `axor-wrap worker` machines and whose each is (QA-504): a worker
    reports no developer id — its person is the owner of the key it reports
    with, named by that key's email."""
    from datetime import timedelta

    from axor_backend.routers.machines_api import ONLINE_SECONDS

    cutoff = (datetime.now(UTC) - timedelta(seconds=ONLINE_SECONDS)).isoformat()
    out = []
    for m in await store.list_machines():
        key = await store.get_api_key(m["key_id"]) if m.get("key_id") else None
        label = str((key or {}).get("label") or "")
        email = label.split(" · ", 1)[0] if (key or {}).get("created_by") and "@" in label else ""
        out.append({"machine_id": m["machine_id"], "host": m["host"],
                    "repo_label": m["repo_label"], "email": email or m["owner"],
                    "online": m["last_seen"] >= cutoff})
    return out


@router.put("/v1/identities/{developer_id}")
async def set_identity(developer_id: str, body: dict, request: Request, store: StoreDep,
                       principal: PrincipalDep) -> dict:
    from axor_backend import audit
    from axor_backend.permissions import require_admin

    require_admin(principal)
    if "@" in developer_id or not developer_id or len(developer_id) > 64:
        raise HTTPException(400, "a machine's developer id: letters, digits, -_. (at most 64)")
    email = body.get("email")
    if email is not None and ("@" not in str(email) or len(str(email)) > 255):
        raise HTTPException(400, "email is a person's address, or null to unbind")
    await store.set_developer_binding(developer_id, str(email) if email else None,
                                      datetime.now(UTC).isoformat())
    await audit.record(store, request, "developer_bound" if email else "developer_unbound",
                       developer_id, **({"email": str(email)} if email else {}))
    return {"developer_id": developer_id, "email": email}
