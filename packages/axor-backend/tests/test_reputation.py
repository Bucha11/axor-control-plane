"""Cross-session reputation reaches the plane (ui-spec:416).

axor-sentinel is the only component in this product that looks across sessions:
it exists to catch exfiltration staged over dozens of individually normal ones,
which per-session detection structurally cannot see. It runs on the NODE, beside
axor-core, and it must — ui-spec §12.0, enforcement stays local and the plane
never enters the decision path.

The plane's half is to render it, and that half did not exist. Measured before
this: no route accepted a snapshot, no table held one, and
`GET /v1/plane/topology` answered with posture and nothing else — so the one
thing in the system that survives between sessions was invisible on the surface
built to show it. Every rule below is axor-sentinel's, imported; this plane
stores and renders.
"""
from __future__ import annotations

import pathlib

import httpx
import pytest
from axor_backend.app import create_app
from axor_sentinel.sentinel.predicates import LEVEL_SUSPICION, ReputationLevel
from axor_sentinel.sentinel.snapshot import ReputationSnapshot, snapshot_payload

NODE = "banking-assistant"


@pytest.fixture
async def client(tmp_path: pathlib.Path) -> httpx.AsyncClient:
    app = create_app(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/axor.db",
        operator_keys={}, allow_unsigned=True,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://backend.test"
    ) as c, app.router.lifespan_context(app):
        c._app = app  # type: ignore[attr-defined]
        yield c


def _snapshot(
    version: int = 7,
    flagged: tuple[str, ...] = ("db:customers",),
    watch: tuple[str, ...] = ("s3:exports",),
    clean: tuple[str, ...] = ("cache:sessions",),
) -> dict:
    reputation = {r: LEVEL_SUSPICION[ReputationLevel.FLAGGED] for r in flagged}
    reputation |= {r: LEVEL_SUSPICION[ReputationLevel.WATCH] for r in watch}
    reputation |= {r: LEVEL_SUSPICION[ReputationLevel.CLEAN] for r in clean}
    level = ({r: "FLAGGED" for r in flagged} | {r: "WATCH" for r in watch}
             | {r: "CLEAN" for r in clean})
    return snapshot_payload(ReputationSnapshot(
        version=version, generated_at=1_700_000_000.0 + version,
        resource_reputation=reputation, resource_level=level,
        verdict_facts={r: ["P3 staging count: 4 tainted sessions / 30d",
                           "P4 staged-then-export"] for r in flagged},
    ).with_checksum())


async def _node(client: httpx.AsyncClient) -> None:
    """A node the plane knows about, so the topology has a row to annotate."""
    r = await client.post(f"/v1/plane/{NODE}/command", json={
        "version": 1, "state": {"paused": False},
        "operator": "op", "timestamp": "", "sig": "",
    })
    assert r.status_code == 202


async def _annotation(client: httpx.AsyncClient) -> dict | None:
    topology = (await client.get("/v1/plane/topology")).json()
    [row] = [n for n in topology["nodes"] if n["node_id"] == NODE]
    return row["reputation"]


# ── the axis arrives ──────────────────────────────────────────────────────────

async def test_a_posted_snapshot_annotates_the_topology(
    client: httpx.AsyncClient,
) -> None:
    await _node(client)
    r = await client.post(f"/v1/plane/{NODE}/reputation", json=_snapshot())
    assert r.status_code == 201
    assert r.json() == {"stored": True, "version": 7, "flagged": 1}

    annotation = await _annotation(client)
    # `received_ts` is when the plane heard it, `generated_at` when the node's
    # cycle computed it — both travel because a stale sentinel and a silent one
    # look identical without the pair.
    assert annotation.pop("received_ts")
    assert annotation == {
        "version": 7, "generated_at": 1_700_000_007.0,
        "flagged": 1, "watch": 1, "clean": 1, "resources": 3,
    }


async def test_the_facts_behind_a_verdict_are_readable(
    client: httpx.AsyncClient,
) -> None:
    """A reputation number on its own is an accusation. What an operator needs
    beside a FLAGGED resource is which predicate fired."""
    await client.post(f"/v1/plane/{NODE}/reputation", json=_snapshot())
    rep = (await client.get(f"/v1/plane/{NODE}/reputation")).json()["reputation"]

    assert rep["resource_level"]["db:customers"] == "FLAGGED"
    assert rep["verdict_facts"]["db:customers"] == [
        "P3 staging count: 4 tainted sessions / 30d", "P4 staged-then-export",
    ]


async def test_reputation_survives_across_reports(client: httpx.AsyncClient) -> None:
    """The point of the axis. A resource that was WATCH is FLAGGED in the next
    cycle because evidence accumulated across sessions the plane never saw."""
    await _node(client)
    await client.post(f"/v1/plane/{NODE}/reputation", json=_snapshot(7))
    assert (await _annotation(client))["flagged"] == 1

    await client.post(f"/v1/plane/{NODE}/reputation", json=_snapshot(
        8, flagged=("db:customers", "s3:exports"), watch=(),
    ))
    annotation = await _annotation(client)
    assert (annotation["flagged"], annotation["watch"]) == (2, 0)


async def test_flagged_resources_page_on_call(client: httpx.AsyncClient) -> None:
    sent: list[tuple[str, str, dict]] = []

    class Spy:
        async def emit(self, trigger: str, node_id: str, payload: dict) -> None:
            sent.append((trigger, node_id, payload))

    client._app.state.notifier = Spy()
    await client.post(f"/v1/plane/{NODE}/reputation", json=_snapshot())

    [(trigger, node, payload)] = sent
    assert (trigger, node) == ("heat_threshold", NODE)
    assert payload["flagged"] == ["db:customers"]
    assert payload["facts"]["db:customers"][0].startswith("P3 staging count")


async def test_a_clean_snapshot_pages_nobody(client: httpx.AsyncClient) -> None:
    sent: list[str] = []

    class Spy:
        async def emit(self, trigger: str, node_id: str, payload: dict) -> None:
            sent.append(trigger)

    client._app.state.notifier = Spy()
    r = await client.post(f"/v1/plane/{NODE}/reputation",
                          json=_snapshot(flagged=(), watch=()))
    assert r.json()["flagged"] == 0
    assert sent == []


# ── absence is not a clean bill ───────────────────────────────────────────────

async def test_a_node_with_no_sentinel_is_not_the_cleanest_node(
    client: httpx.AsyncClient,
) -> None:
    """`null`, not `flagged: 0`. "Nobody is watching this node across sessions"
    and "somebody is watching and found nothing" are opposite facts; collapsing
    them would make an unwatched node render as the safest thing on the graph."""
    await _node(client)
    assert await _annotation(client) is None
    assert (await client.get(f"/v1/plane/{NODE}/reputation")).json() == {
        "reputation": None,
    }

    await client.post(f"/v1/plane/{NODE}/reputation",
                      json=_snapshot(flagged=(), watch=()))
    watched = await _annotation(client)
    assert watched is not None and watched["flagged"] == 0


# ── what the plane refuses ────────────────────────────────────────────────────

async def test_a_rewritten_snapshot_is_refused(client: httpx.AsyncClient) -> None:
    """Over a wire the maps and their checksum arrive together from a party the
    plane is not. Clearing a FLAGGED resource is the edit worth making, and the
    checksum is the only thing that catches it. The rule is axor-sentinel's."""
    tampered = _snapshot()
    tampered["resource_reputation"]["db:customers"] = 0.0
    r = await client.post(f"/v1/plane/{NODE}/reputation", json=tampered)

    assert r.status_code == 400
    assert "checksum" in r.json()["detail"]
    assert (await client.get(f"/v1/plane/{NODE}/reputation")).json()["reputation"] is None


async def test_a_calibrated_float_is_not_a_verdict(client: httpx.AsyncClient) -> None:
    """The codomain is finite so core's `detection_floor` comparison is
    decidable end to end. The plane holds that line with axor-sentinel's set,
    not a threshold of its own."""
    calibrated = _snapshot()
    calibrated["resource_reputation"]["db:customers"] = 0.73
    r = await client.post(f"/v1/plane/{NODE}/reputation", json=calibrated)

    assert r.status_code == 400
    assert "finite codomain" in r.json()["detail"]


async def test_a_reordered_snapshot_cannot_walk_reputation_backwards(
    client: httpx.AsyncClient,
) -> None:
    """Versions are monotonic at the sentinel that wrote them, so an older one
    arriving later is a retry or a reorder. The direction that matters is the
    one where a FLAGGED resource quietly returns to clean."""
    await _node(client)
    await client.post(f"/v1/plane/{NODE}/reputation", json=_snapshot(8))
    r = await client.post(f"/v1/plane/{NODE}/reputation",
                          json=_snapshot(6, flagged=(), watch=()))

    assert r.status_code == 409
    assert "not newer than the version 8" in r.json()["detail"]
    assert (await _annotation(client))["flagged"] == 1


async def test_the_same_version_twice_is_not_news(client: httpx.AsyncClient) -> None:
    await client.post(f"/v1/plane/{NODE}/reputation", json=_snapshot(7))
    r = await client.post(f"/v1/plane/{NODE}/reputation", json=_snapshot(7))
    assert r.status_code == 409


async def test_a_payload_that_is_not_a_snapshot_is_refused(
    client: httpx.AsyncClient,
) -> None:
    r = await client.post(f"/v1/plane/{NODE}/reputation",
                          json={"version": 1, "resource_reputation": {}})
    assert r.status_code == 400
    assert "reputation snapshot" in r.json()["detail"]


# ── tenancy ───────────────────────────────────────────────────────────────────

async def test_one_row_per_node_not_a_series(client: httpx.AsyncClient) -> None:
    """A snapshot is a complete statement of what a sentinel currently
    believes. The history behind it is the sentinel's own append-only evidence
    sets, on the node, where the facts are."""
    store = client._app.state.store
    for version in (3, 4, 5):
        await client.post(f"/v1/plane/{NODE}/reputation", json=_snapshot(version))
    assert (await store.reputation(NODE))["version"] == 5
    assert list(await store.all_reputation()) == [NODE]
