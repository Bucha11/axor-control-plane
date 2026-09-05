"""Storage-layer invariants that were not holding.

Each test names a defect the store actually had. They are grouped here rather
than spread across the feature suites because none of them is about a feature —
they are about the guarantees every feature above assumes: that a trace comes
back in the order it happened, that an acknowledged command was applied, that
one tenant cannot delete another's rows, and that a duplicate is a duplicate
rather than a 500.
"""
from __future__ import annotations

import asyncio
import json
import pathlib

import pytest
from axor_backend.errors import ConcurrentUpdate
from axor_backend.storage import Store, init_db, make_engine
from axor_backend.tenancy import PUBLIC_ORG, set_current_org


@pytest.fixture
async def store(tmp_path: pathlib.Path) -> Store:
    s = Store(make_engine(f"sqlite+aiosqlite:///{tmp_path}/s.db"))
    await init_db(s.engine)
    set_current_org(PUBLIC_ORG)
    yield s
    set_current_org(PUBLIC_ORG)


def _line(node: str, seq: int, kind: str = "tool_call") -> dict:
    return {"node_id": node, "seq": seq, "kind": kind,
            "schema_version": "1", "payload": {}}


# ── event order ───────────────────────────────────────────────────────────────

async def test_a_multi_node_trace_reads_back_in_the_order_it_happened(
    store: Store,
) -> None:
    """seq is monotonic PER NODE, so ordering a multi-node run by seq
    interleaves the nodes and breaks ties on whatever the index yields. On the
    demo tree that put a message_received five positions ahead of the
    message_sent that caused it — in a trace whose promise is that replay
    reproduces what was recorded."""
    from axor_backend import demo

    await store.upsert_run("ex_tree", demo.TREE_ORCH, "d", "2026-01-01T00:00:00")
    await store.ingest_events("ex_tree", demo.TREE_ORCH, demo.TREE_EVENTS, None)

    authored = [(e["node_id"], e["seq"]) for e in demo.TREE_EVENTS]
    stored = [
        (json.loads(raw)["node_id"], json.loads(raw)["seq"])
        for raw in await store.run_events("ex_tree")
    ]
    assert stored == authored

    # And causally: nothing is received before anything is sent.
    lines = [json.loads(raw) for raw in await store.run_events("ex_tree")]
    first_send = next(i for i, e in enumerate(lines) if e["kind"] == "message_sent")
    first_recv = next(i for i, e in enumerate(lines) if e["kind"] == "message_received")
    assert first_send < first_recv


async def test_the_stream_cursor_is_the_event_id_not_the_seq(store: Store) -> None:
    """A reconnect resumes from Last-Event-ID. With per-node seq as the id the
    cursor meant something different for every node, so a resume dropped
    whatever the lagging nodes had numbered below it."""
    await store.upsert_run("r", "a", "s", "2026-01-01T00:00:00")
    await store.ingest_events("r", "a", [
        _line("a", 0), _line("b", 0), _line("a", 1), _line("b", 1),
    ], None)

    page = await store.run_events_after("r", 0)
    assert len(page) == 4
    ids = [event_id for event_id, _ in page]
    assert ids == sorted(ids), "cursor must be monotonic"
    # Resuming after the second event yields exactly the remaining two — not
    # "everything whose seq is above 0", which would have dropped b/1.
    assert len(await store.run_events_after("r", ids[1])) == 2


async def test_ingest_returns_the_ids_it_stored_and_nothing_else(
    store: Store,
) -> None:
    """The audit stream publishes what was stored, with its cursor. A duplicate
    batch stores nothing, so it must broadcast nothing."""
    await store.upsert_run("r", "a", "s", "2026-01-01T00:00:00")
    first = await store.ingest_events("r", "a", [_line("a", 0)], "batch-1")
    assert [line for _, line in first] == [_line("a", 0)]
    assert await store.ingest_events("r", "a", [_line("a", 0)], "batch-1") == []


# ── deduplication ─────────────────────────────────────────────────────────────

async def test_a_duplicate_inside_one_batch_is_deduped_not_a_500(
    store: Store,
) -> None:
    """ingest_events promises dedupe on (node_id, seq). It checked only against
    what was already stored, never against the batch itself, so a client that
    repeated a line inside one request got an IntegrityError and a 500."""
    await store.upsert_run("r", "a", "s", "2026-01-01T00:00:00")
    stored = await store.ingest_events("r", "a", [_line("a", 0), _line("a", 0)], None)
    assert len(stored) == 1
    assert len(await store.run_events("r")) == 1


async def test_concurrent_creates_of_one_run_do_not_raise(store: Store) -> None:
    """Check-then-act: eight requests all see "absent" and all insert. The
    composite primary key is the arbiter, and losing that race means the row
    exists — which is what the caller asked for."""
    await asyncio.gather(*[
        store.upsert_run("racy", "a", "s", "2026-01-01T00:00:00") for _ in range(8)
    ])
    assert (await store.get_run("racy"))["run_id"] == "racy"


# ── desired state: the command channel ────────────────────────────────────────

async def test_concurrent_commands_do_not_lose_one_another(store: Store) -> None:
    """The read-modify-write on desired state had no version condition, so two
    commands landing together both read version N and both wrote N+1 — one
    operator's delta vanished after the plane had already answered 202. A
    command that is acknowledged and never applied is the one failure this
    channel must not have."""
    await store.bump_desired("n1", {"base": 1})
    versions = await asyncio.gather(
        store.bump_desired("n1", {"a": 1}),
        store.bump_desired("n1", {"b": 2}),
    )
    version, state = await store.get_desired("n1")
    assert state == {"base": 1, "a": 1, "b": 2}, "a delta was lost"
    assert version == 3, "each command must advance the version exactly once"
    assert sorted(v for v, _ in versions) == [2, 3]


async def test_a_repeated_consumption_ack_does_not_bump_the_version(
    store: Store,
) -> None:
    """The adapter re-fetches on every version change. Acking a one-shot that is
    already gone must be a no-op, or it fetches a state byte-identical to the
    one it just applied."""
    await store.bump_desired("n1", {"inject": "x"})
    await store.clear_desired_key("n1", "inject")
    version, state = await store.get_desired("n1")
    assert state == {}
    await store.clear_desired_key("n1", "inject")
    assert (await store.get_desired("n1"))[0] == version


async def test_an_ack_for_an_uncommanded_node_is_a_no_op(store: Store) -> None:
    await store.clear_desired_key("never-commanded", "inject")
    assert await store.get_desired("never-commanded") is None


async def test_exhausted_retries_raise_rather_than_losing_a_command(
    store: Store,
) -> None:
    """Surfacing beats returning quietly: a silent give-up would be exactly the
    lost update the retries exist to prevent."""
    await store.bump_desired("n1", {"base": 1})
    with pytest.raises(ConcurrentUpdate, match="was not applied"):
        await store._merge_desired(  # noqa: SLF001
            "n1", lambda state: {**state, "x": 1}, attempts=0,
        )
    # And nothing was written on the way out.
    assert (await store.get_desired("n1"))[1] == {"base": 1}


async def test_a_dozen_simultaneous_commands_all_land(store: Store) -> None:
    """The two-command case can pass by luck of scheduling; this one cannot.
    Every delta must survive and the version must advance exactly once per
    command, which is only true if the losers re-read and re-merge."""
    await store.bump_desired("n1", {"base": 1})
    await asyncio.gather(*[
        store.bump_desired("n1", {f"k{i}": i}) for i in range(12)
    ])
    version, state = await store.get_desired("n1")
    assert state == {"base": 1, **{f"k{i}": i for i in range(12)}}
    assert version == 13


# ── dead letters: one tenant must not erase another's ─────────────────────────

async def test_the_dead_letter_cap_is_per_tenant(store: Store) -> None:
    """The cap was global, which made this the one table where a tenant could
    destroy another tenant's data — and a dead letter is precisely the evidence
    that deliveries were lost."""
    for i in range(6):
        await store.add_dead_letter(f"http://a/{i}", {"n": i}, "e", 1,
                                    f"t{i}", cap=5, org="org_a")
    set_current_org("org_a")
    assert len(await store.list_dead_letters()) == 5

    for i in range(20):
        await store.add_dead_letter(f"http://b/{i}", {"n": i}, "e", 1,
                                    f"t{i}", cap=5, org="org_b")

    set_current_org("org_a")
    assert len(await store.list_dead_letters()) == 5, "org_b evicted org_a's rows"
    set_current_org("org_b")
    assert len(await store.list_dead_letters()) == 5, "org_b's own cap still holds"


# ── single-row reads ──────────────────────────────────────────────────────────

async def test_get_run_reads_one_row(store: Store) -> None:
    """Callers that need one run used to build a dict from list_runs() — the
    whole table, with every EvidenceCase blob — to index into it. One of those
    callers serves the unauthenticated GET /v1/share/{token}."""
    for i in range(5):
        await store.upsert_run(f"r{i}", "n", "s", f"2026-01-01T00:00:0{i}")
    await store.set_evidence("r2", [{"deviation": "d"}])

    assert (await store.get_run("r2"))["evidence"] == [{"deviation": "d"}]
    assert await store.get_run("nope") is None

    # And it is tenant-scoped like every other read.
    set_current_org("other-org")
    assert await store.get_run("r2") is None
