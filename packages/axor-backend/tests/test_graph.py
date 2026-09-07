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


# ── the persistent store is rebuilt into on every boot ───────────────────────

class TestARestartFoldsTheLogAgain:
    """The graph is a DERIVED index: `rehydrate_graph` re-folds every run and
    every fact at boot. On a persistent store that means every write here is
    performed again, and again, for the life of the deployment.

    Both writes used `CREATE`. Derivations therefore duplicated on every
    restart, and attestations — whose `fact_id` is a primary key — raised on the
    second one. `rehydrate` is awaited in lifespan with no handler, so a hosted
    deployment where an operator had ever attested a branch did not start again.
    """

    @staticmethod
    def _fact(fact_id: str = "f1") -> str:
        return json.dumps({"fact_id": fact_id, "operator": "op",
                           "reason": "checked", "covers": ["v2"]})

    async def test_folding_a_derivation_again_does_not_duplicate_it(
        self, store: KuzuGraphStore,
    ) -> None:
        for _ in range(4):
            await store.register_derivation("v1", "v2", "run1")
        assert len((await store.khop("v1", 2, 100))["edges"]) == 1

    async def test_two_runs_over_the_same_pair_are_two_edges(
        self, store: KuzuGraphStore,
    ) -> None:
        """Deduplication is on the whole edge, not on the pair: the run id is
        what links an edge back to its EvidenceCase, so the same derivation seen
        in two runs is two facts."""
        await store.register_derivation("v1", "v2", "run1")
        await store.register_derivation("v1", "v2", "run2")
        runs = {e["run_id"] for e in (await store.khop("v1", 2, 100))["edges"]}
        assert runs == {"run1", "run2"}

    async def test_folding_an_attestation_again_neither_raises_nor_doubles(
        self, store: KuzuGraphStore,
    ) -> None:
        for _ in range(3):
            await store.append_attestation(self._fact())
        assert len(await store.branch_attestations("v2")) == 1

    async def test_a_deployment_boots_more_than_once(
        self, tmp_path: pathlib.Path,
    ) -> None:
        """The whole thing, on one DB directory: what a restart actually does."""
        from axor_backend.graph import rehydrate_graph

        events = [
            {"kind": "tool_call", "node_id": "A",
             "payload": {"arg_refs": {"a": "v1"}}},
            {"kind": "tool_result", "node_id": "A", "payload": {"value_ref": "v2"}},
        ]

        class Log:
            async def list_runs(self) -> list[dict]:
                return [{"run_id": "r1"}]

            async def run_events(self, run_id: str) -> list[str]:
                return [json.dumps(e) for e in events]

            async def all_facts(self) -> list[dict]:
                return [{"fact_type": "operator_attestation", "fact_id": "f1",
                         "operator": "op", "reason": "checked", "covers": ["v2"]}]

        for _ in range(3):
            store = KuzuGraphStore(tmp_path, tenant="boots")
            await rehydrate_graph(Log(), store)
            assert len((await store.khop("v1", 2, 100))["edges"]) == 1
            assert len(await store.branch_attestations("v2")) == 1
            store.close()


class TestTheRegistryBoundsOpenDatabasesWithoutLosingOne:
    """A store per tenant, held for the life of the process, is an open database
    handle per tenant that ever asked. `max_open` bounds that — but only for a
    store whose data survives being closed."""

    async def test_a_persistent_store_is_evicted_and_reopens_intact(
        self, tmp_path: pathlib.Path,
    ) -> None:
        from axor_backend.graph import GraphRegistry

        registry = GraphRegistry(
            factory=lambda org: KuzuGraphStore(tmp_path, org), max_open=2)
        for org in ("a", "b", "c"):
            await registry.for_org(org).register_derivation(f"{org}1", f"{org}2", "r")
        assert registry.tenants == ("b", "c")
        reopened = await registry.for_org("a").khop("a1", 2, 100)
        assert len(reopened["edges"]) == 1

    async def test_an_in_memory_store_is_never_evicted(self) -> None:
        """It IS the data — nothing rebuilds it outside boot, so dropping one
        would silently empty a tenant's provenance graph. Holding the handle is
        the lesser cost, and the cap simply does not apply."""
        from axor_backend.graph import GraphRegistry

        registry = GraphRegistry(max_open=1)
        for org in ("x", "y"):
            await registry.for_org(org).register_derivation(f"{org}1", f"{org}2", "r")
        assert registry.tenants == ("x", "y")
        assert len((await registry.for_org("x").khop("x1", 2, 100))["edges"]) == 1
