"""Value provenance and operator attestations, both scoped to ONE run.

Neither is stored. Provenance is recomputed from the run's own events
(:mod:`axor_backend.provenance`) and attestations are read from the fact log,
which is where they already lived — the same posture as
``/v1/runs/{run_id}/subgraph`` next door: a derived answer belongs to the rows
it is derived from, not to a second copy that has to be kept true.

The run in the path is not decoration. Value refs are minted per trace from a
counter that restarts at zero, so ``v_ext_1`` names a different value in every
run; an answer given for a bare ref is an answer about all of them at once.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from axor_backend import provenance
from axor_backend.attestations import covering
from axor_backend.deps import StoreDep
from axor_backend.limits import MAX_KHOP_K, MAX_KHOP_LIMIT
from axor_backend.traces import events_for

router = APIRouter(prefix="/v1/runs", tags=["provenance"])


@router.get("/{run_id}/provenance")
async def run_provenance(
    run_id: str,
    focus: str,
    store: StoreDep,
    k: int = Query(2, ge=1, le=MAX_KHOP_K),
    limit: int = Query(100, ge=1, le=MAX_KHOP_LIMIT),
) -> dict:
    """k-hop neighbourhood of a value ref within this run, expand-on-click.

    `k` and `limit` are bounded here rather than trusted — a caller asking for
    too much gets a 422 naming the bound, not a walk sized by the request.
    """
    return provenance.khop(await events_for(store, run_id), focus, k, limit)


@router.get("/{run_id}/attestations")
async def run_attestations(run_id: str, ref: str, store: StoreDep) -> list[dict]:
    """The attestation history of one branch in this run, newest first.

    Both directions: an attestation and the revocation that ended it both stay
    (append-only, nothing is deleted), and ``in_effect`` says which coverage
    still stands — computed by Sentinel's rule, not by a second one here.
    """
    return covering(await store.attestation_facts(run_id), ref)
