"""Kuzu GraphStore spike (bundle loose end 4): schema, derivations, k-hop cut,
append-only attestations. Skipped when the kuzu wheel is unavailable."""
from __future__ import annotations

import json
import pathlib

import pytest

kuzu = pytest.importorskip("kuzu")

from axor_backend.graph import KuzuGraphStore  # noqa: E402


@pytest.fixture
async def store(tmp_path: pathlib.Path) -> KuzuGraphStore:
    return KuzuGraphStore(tmp_path, tenant="t1")


async def test_derivations_and_khop(store: KuzuGraphStore) -> None:
    await store.register_derivation("v_mail", "v_summary", "run_1")
    await store.register_derivation("v_summary", "v_export", "run_1")
    await store.register_derivation("v_other", "v_far", "run_2")

    one_hop = await store.khop("v_mail", k=1, limit=50)
    assert set(one_hop["nodes"]) == {"v_mail", "v_summary"}

    two_hop = await store.khop("v_mail", k=2, limit=50)
    assert set(two_hop["nodes"]) == {"v_mail", "v_summary", "v_export"}
    assert {"src": "v_mail", "dst": "v_summary", "run_id": "run_1"} in two_hop["edges"]
    assert "v_other" not in two_hop["nodes"]  # focus cut, not the whole graph


async def test_attestation_is_append_only_node(store: KuzuGraphStore) -> None:
    await store.register_derivation("v_mail", "v_summary", "run_1")
    await store.append_attestation(json.dumps({
        "fact_id": "a1", "fact_type": "operator_attestation",
        "covers": ["v_mail"], "operator": "op_d",
        "reason": "checked; internal notification", "sig": "ed25519:xx",
    }))
    atts = await store.branch_attestations("v_mail")
    assert atts == [{"fact_id": "a1", "operator": "op_d",
                     "reason": "checked; internal notification",
                     "revokes": None}]
    # revocation is a NEW event, not a deletion
    await store.append_attestation(json.dumps({
        "fact_id": "a2", "fact_type": "operator_attestation",
        "covers": ["v_mail"], "operator": "op_d",
        "reason": "reopening", "revokes": "a1", "sig": "ed25519:yy",
    }))
    atts = await store.branch_attestations("v_mail")
    assert {a["fact_id"] for a in atts} == {"a1", "a2"}
