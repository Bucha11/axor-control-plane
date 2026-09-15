"""Self-heal, end to end: the cut the node actually receives, and the re-probe
that says whether it worked (ui-spec 8.2.1).

Before this, the loop was open in three places at once. The node could localize
the drift and had nowhere to post what it found; the health panel's "Self-heal"
button appended an `operator_attestation` fact — a record that somebody
intervened, next to a context nothing had touched — and then said it was
"awaiting the verifying re-probe"; and the re-probe, when it arrived, was
stored as one more battery with nothing to fold it into. Measured on the loop
as it stood: after a heal, `get_desired` was `None`, the adapter's
`take_pending_excision` returned `None` with an empty outbox, and a CONSISTENT
re-probe resolved nothing anywhere.

The derivations all belong to axor-probe: `proposal_from_payload` rebuilds what
the localizer found, `excision_request` decides what may be cut, `heal_outcome`
decides what counts as healed. This plane stores, delivers and pairs them up.
"""
from __future__ import annotations

import pathlib

import httpx
import pytest
from axor_backend.app import create_app
from axor_probe.integration import plane as probe_plane
from axor_probe.repair.localize import Fragment, RepairProposal, localize
from axor_wrap.plane.session import PlaneSession

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


def _proposal(escapes: str = "frag_web_3", mixed: bool = False) -> RepairProposal:
    """A real localizer run: `escapes` alone causes the escape. With `mixed`,
    the culprit also carries legitimate task content, so it is escalated."""
    frags = [
        Fragment("frag_web_3", pure_tainted=not mixed),
        Fragment("frag_web_7", pure_tainted=True),
        Fragment("frag_task_1", pure_tainted=False),
    ]
    return localize(frags, lambda present: escapes in present)


def _battery(
    verdict: str = "DRIFT_DETECTED", escaped: bool = True,
    session_id: str = "s1", proposal: object = None,
) -> dict:
    body = {
        "session_id": session_id, "agent_id": NODE, "model": "m",
        "probe_library_version": "1.0.0", "overall_verdict": verdict,
        "families": [
            {"family": "data_disclosure",
             "state": "escaped" if escaped else "clean",
             "escapes": 2 if escaped else 0, "probes": 4},
            {"family": "scope_expansion", "state": "clean",
             "escapes": 0, "probes": 4},
        ],
        "probes_sent": 8, "probes_invalid": 0, "probes_triangulated": 0,
        "structural_failures": 0, "escape_count": 2 if escaped else 0,
        "escape_rate": 0.25 if escaped else 0.0, "escape_rate_ci": [0.0, 0.6],
        "calibration_status": "UNCALIBRATED", "max_drift_score_uncalibrated": 0.7,
    }
    if proposal is not None:
        body["repair_proposal"] = probe_plane.proposal_payload(proposal)
    return body


async def _drift(client: httpx.AsyncClient, **kw: object) -> None:
    r = await client.post(f"/v1/plane/{NODE}/probe-report",
                          json=_battery(proposal=_proposal(), **kw))
    assert r.status_code == 201, r.text


async def _cut(client: httpx.AsyncClient, **body: object) -> dict:
    """Shape a cut and command it — the two halves the UI performs."""
    r = await client.post(f"/v1/plane/{NODE}/repair/excision-request",
                          json={"reason": "drift after retrieval", **body})
    assert r.status_code == 200, r.text
    shaped = r.json()
    r = await client.post(f"/v1/plane/{NODE}/command", json={
        "version": shaped["version"], "state": shaped["state"],
        "operator": "op_ui", "timestamp": "", "sig": "",
    })
    assert r.status_code == 202, r.text
    return shaped["state"]["pending_excision"]


# ── the cut reaches the node ──────────────────────────────────────────────────

async def test_the_heal_is_a_cut_the_node_receives(client: httpx.AsyncClient) -> None:
    """The whole point: an adapter subscribing after the heal is handed the
    excision and removes the fragment. This is the assertion the old self-heal
    failed — `take_pending_excision` returned None and the outbox was empty."""
    await _drift(client)
    body = await _cut(client)

    version, state = await client._app.state.store.get_desired(NODE)
    session = PlaneSession(node_id=NODE)
    session.apply_snapshot(version, state)
    taken = session.take_pending_excision({"frag_web_3": "runtime"})

    assert taken is not None
    assert taken.excision_id == body["id"]
    assert taken.target_refs == ("frag_web_3",)
    assert taken.reason == "drift after retrieval"
    assert [e["kind"] for e in session.outbox] == ["context_excision"]


async def test_the_shaping_route_commands_nothing(client: httpx.AsyncClient) -> None:
    """`/repair/excision-request` returns a body to sign. It must not be a
    second way into desired state — on a signed deployment that would be the
    unsigned way."""
    await _drift(client)
    r = await client.post(f"/v1/plane/{NODE}/repair/excision-request",
                          json={"reason": "drift after retrieval"})
    assert r.status_code == 200
    assert await client._app.state.store.get_desired(NODE) is None
    assert (await client.get(f"/v1/plane/{NODE}/repair")).json()["pending"] is None


async def test_a_signed_deployment_signs_the_cut_like_any_command(
    tmp_path: pathlib.Path,
) -> None:
    """Same route, same signature check. The shaped body is worth nothing on
    its own — it becomes an excision only through the door that verifies."""
    app = create_app(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/axor.db",
        operator_keys={"op_ui": "3b6a27bcceb6a42d62a3a8d02a6f0d73653215771de243a63ac048a18b59da29"},
        allow_unsigned=False,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://backend.test"
    ) as c, app.router.lifespan_context(app):
        await c.post(f"/v1/plane/{NODE}/probe-report",
                     json=_battery(proposal=_proposal()))
        r = await c.post(f"/v1/plane/{NODE}/repair/excision-request",
                         json={"reason": "drift after retrieval"})
        assert r.status_code == 200
        shaped = r.json()
        r = await c.post(f"/v1/plane/{NODE}/command", json={
            "version": shaped["version"], "state": shaped["state"],
            "operator": "op_ui", "timestamp": "", "sig": "",
        })
        assert r.status_code == 403
        assert await app.state.store.get_desired(NODE) is None


# ── the verifying re-probe ────────────────────────────────────────────────────

async def test_the_reprobe_closes_the_pair(client: httpx.AsyncClient) -> None:
    await _drift(client)
    body = await _cut(client)

    r = await client.post(f"/v1/plane/{NODE}/probe-report",
                          json=_battery("CONSISTENT", escaped=False, session_id="s2"))
    assert r.json()["heals_verified"] == [{
        "excision_id": body["id"], "operator": "op_ui",
        "healed_families": ["data_disclosure"],
        "reprobe_verdict": "CONSISTENT", "resolved": True,
        "caption": "healed by op_ui → re-probe: OK",
    }]
    history = (await client.get(f"/v1/plane/{NODE}/repair")).json()["history"]
    assert [(h["excision_id"], h["resolved"]) for h in history] == [(body["id"], True)]


async def test_a_cut_that_did_not_work_is_not_resolved(client: httpx.AsyncClient) -> None:
    """No optimistic green. The heal happened; the drift did not go away."""
    await _drift(client)
    await _cut(client)
    r = await client.post(f"/v1/plane/{NODE}/probe-report",
                          json=_battery(session_id="s2"))
    [outcome] = r.json()["heals_verified"]
    assert outcome["resolved"] is False
    assert outcome["caption"] == "healed by op_ui → re-probe: still drifting"


async def test_a_heal_awaiting_its_reprobe_is_neither_resolved_nor_failed(
    client: httpx.AsyncClient,
) -> None:
    """Three states, not two: `reprobe_verdict` is null while the node has not
    reported back. Rendering that as `resolved=false` would make a cut nobody
    has measured yet look like a cut that failed."""
    await _drift(client)
    await _cut(client)
    [pending] = (await client.get(f"/v1/plane/{NODE}/repair")).json()["history"]
    assert pending["reprobe_verdict"] is None
    assert pending["resolved"] is None


async def test_only_the_first_report_after_a_cut_verifies_it(
    client: httpx.AsyncClient,
) -> None:
    """A later battery describes a later state of the node. If it could rewrite
    the closed verdict, a cut that failed would turn green on its own the next
    time the agent happened to probe clean — which is the optimistic green this
    whole surface exists to refuse."""
    await _drift(client)
    await _cut(client)
    await client.post(f"/v1/plane/{NODE}/probe-report", json=_battery(session_id="s2"))
    r = await client.post(f"/v1/plane/{NODE}/probe-report",
                          json=_battery("CONSISTENT", escaped=False, session_id="s3"))

    assert r.json()["heals_verified"] == []
    [heal] = (await client.get(f"/v1/plane/{NODE}/repair")).json()["history"]
    assert heal["reprobe_verdict"] == "DRIFT_DETECTED" and heal["resolved"] is False


async def test_closing_a_closed_heal_writes_nothing(
    client: httpx.AsyncClient,
) -> None:
    """At the store seam, because the route cannot reach it: `_close_heals`
    iterates only the OPEN attempts, so through the API a second close never
    happens. The condition is on the UPDATE for the case that has no route —
    two reports landing together, both reading the same open row. Every other
    write on this channel is conditional for the same reason.
    """
    store = client._app.state.store
    attempt = await store.open_heal_attempt(
        NODE, "exc_1", "op_ui", "r", ["frag_web_3"], ["data_disclosure"], "t0",
    )
    await store.close_heal_attempt(attempt, "DRIFT_DETECTED", False, "t1")
    await store.close_heal_attempt(attempt, "CONSISTENT", True, "t2")

    [heal] = await store.heal_attempt_history(NODE)
    assert heal["reprobe_verdict"] == "DRIFT_DETECTED"
    assert heal["resolved"] is False and heal["outcome_ts"] == "t1"


async def test_the_outcome_names_the_families_the_cut_was_for(
    client: httpx.AsyncClient,
) -> None:
    """`healed_families` is read from the report that showed the drift, not
    recomputed from the re-probe — the re-probe is the one battery guaranteed
    not to show it."""
    await _drift(client)
    await _cut(client)
    r = await client.post(f"/v1/plane/{NODE}/probe-report",
                          json=_battery("CONSISTENT", escaped=False, session_id="s2"))
    assert r.json()["heals_verified"][0]["healed_families"] == ["data_disclosure"]


# ── what the plane refuses ────────────────────────────────────────────────────

async def test_no_proposal_means_there_is_nothing_to_excise(
    client: httpx.AsyncClient,
) -> None:
    """A battery without a localizer run. The panel must not offer a cut with
    no verdict behind it, so the route refuses rather than inventing refs."""
    r = await client.post(f"/v1/plane/{NODE}/probe-report", json=_battery())
    assert r.status_code == 201
    r = await client.post(f"/v1/plane/{NODE}/repair/excision-request",
                          json={"reason": "just do something"})
    assert r.status_code == 409
    assert "did not localize" in r.json()["detail"]


async def test_a_reason_is_required(client: httpx.AsyncClient) -> None:
    await _drift(client)
    r = await client.post(f"/v1/plane/{NODE}/repair/excision-request",
                          json={"reason": "   "})
    assert r.status_code == 400


async def test_an_escalated_fragment_needs_the_operator_to_say_so(
    client: httpx.AsyncClient,
) -> None:
    """The culprit also carries legitimate task content, so cutting it has
    collateral. axor-probe escalates; the plane does not decide otherwise."""
    r = await client.post(f"/v1/plane/{NODE}/probe-report",
                          json=_battery(proposal=_proposal(mixed=True)))
    assert r.status_code == 201
    r = await client.post(f"/v1/plane/{NODE}/repair/excision-request",
                          json={"reason": "cut it"})
    assert r.status_code == 422
    assert "escalates to operator" in r.json()["detail"]

    r = await client.post(f"/v1/plane/{NODE}/repair/excision-request",
                          json={"reason": "cut it", "include_escalated": True})
    assert r.status_code == 200
    assert r.json()["state"]["pending_excision"]["target_refs"] == ["frag_web_3"]


async def test_a_proposal_that_is_not_one_is_refused_while_the_node_can_hear_it(
    client: httpx.AsyncClient,
) -> None:
    body = _battery()
    body["repair_proposal"] = {"verdict": "wipe_everything"}
    r = await client.post(f"/v1/plane/{NODE}/probe-report", json=body)
    assert r.status_code == 400
    assert "repair_proposal" in r.json()["detail"]
    assert (await client.get(f"/v1/plane/{NODE}/probe-report")).json()["latest"] is None


# ── the pair closes for every door into desired state ─────────────────────────

async def test_a_cut_commanded_without_the_shaping_route_is_still_verified(
    client: httpx.AsyncClient,
) -> None:
    """The hook is on the write that reaches the node, not on the surface that
    offers it. An operator with curl gets the same pair as the health panel."""
    await _drift(client)
    r = await client.post(f"/v1/plane/{NODE}/command", json={
        "version": 1, "operator": "op_cli", "timestamp": "", "sig": "",
        "state": {"pending_excision": {
            "id": "exc_by_hand", "target_refs": ["frag_web_3"],
            "reason": "by hand", "operator": "op_cli",
        }},
    })
    assert r.status_code == 202
    r = await client.post(f"/v1/plane/{NODE}/probe-report",
                          json=_battery("CONSISTENT", escaped=False, session_id="s2"))
    assert [o["excision_id"] for o in r.json()["heals_verified"]] == ["exc_by_hand"]


async def test_an_excision_with_no_id_is_refused(client: httpx.AsyncClient) -> None:
    """The adapter keys at-most-once off the id (protocol §4). Without one the
    cut is not a one-shot and its heal can never be paired to a re-probe, so it
    is refused rather than delivered untracked."""
    await _drift(client)
    r = await client.post(f"/v1/plane/{NODE}/command", json={
        "version": 1, "operator": "op_cli", "timestamp": "", "sig": "",
        "state": {"pending_excision": {"target_refs": ["frag_web_3"],
                                       "reason": "r", "operator": "op_cli"}},
    })
    assert r.status_code == 400
    assert "requires an `id`" in r.json()["detail"]


async def test_the_same_cut_commanded_twice_is_one_heal(
    client: httpx.AsyncClient,
) -> None:
    """A re-sent command at a new version is the same one-shot, by id. Two open
    attempts would leave one that no re-probe ever closes."""
    await _drift(client)
    body = await _cut(client)
    r = await client.post(f"/v1/plane/{NODE}/command", json={
        "version": 2, "operator": "op_ui", "timestamp": "", "sig": "",
        "state": {"pending_excision": body},
    })
    assert r.status_code == 202
    r = await client.post(f"/v1/plane/{NODE}/probe-report",
                          json=_battery("CONSISTENT", escaped=False, session_id="s2"))
    assert len(r.json()["heals_verified"]) == 1
    assert len((await client.get(f"/v1/plane/{NODE}/repair")).json()["history"]) == 1


async def test_an_ordinary_command_opens_no_heal(client: httpx.AsyncClient) -> None:
    await _drift(client)
    r = await client.post(f"/v1/plane/{NODE}/command", json={
        "version": 1, "operator": "op_ui", "timestamp": "", "sig": "",
        "state": {"paused": True},
    })
    assert r.status_code == 202
    assert (await client.get(f"/v1/plane/{NODE}/repair")).json()["history"] == []
