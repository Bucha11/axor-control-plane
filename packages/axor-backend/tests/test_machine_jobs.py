"""A developer runs a skill eval from the UI on their own machine.

The machine is their `axor-wrap worker`, reporting with their personal key; the
job starts a coding agent with permissions bypassed and a shell command they
wrote. So the rule every route keeps: a job runs only on its owner's machine.
"""
from __future__ import annotations

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_identity_auth import _bearer, _mint, client, priv  # noqa: F401

pytest.importorskip("jwt")

SPEC = {"skills": ["./.claude/skills/changelog"], "trials": 2, "agent": "claude-code",
        "tasks": [{"prompt": "fix the failing test", "check": "python -m pytest -q"}]}


async def _developer(client: httpx.AsyncClient, priv: Ed25519PrivateKey,  # noqa: F811
                     who: str) -> tuple[dict, dict]:
    """(their browser session, their machine's personal key)"""
    me = _bearer(_mint(priv, role="member", sub=f"usr_{who}", eml=f"{who}@acme.io"))
    key = (await client.post("/v1/keys/mine", headers=me)).json()["secret"]
    return me, _bearer(key)


async def _machine(client: httpx.AsyncClient, key: dict, machine_id: str = "m-laptop") -> None:
    r = await client.post("/v1/machines/heartbeat", headers=key, json={
        "machine_id": machine_id, "host": "laptop", "repo": "acme/api",
        "agents": ["claude-code", "something-else"]})
    assert r.status_code == 200, r.text


async def _queue(client: httpx.AsyncClient, me: dict, machine_id: str = "m-laptop",
                 spec: dict = SPEC) -> httpx.Response:
    return await client.post("/v1/machine-jobs", headers=me,
                              json={"machine_id": machine_id, "kind": "skill_eval", "spec": spec})


async def test_a_job_goes_from_the_ui_to_the_machine_and_back(
        client: httpx.AsyncClient, priv: Ed25519PrivateKey) -> None:  # noqa: F811
    me, key = await _developer(client, priv, "ada")
    await _machine(client, key)
    (machine,) = (await client.get("/v1/machines", headers=me)).json()["machines"]
    assert machine["online"] and machine["yours"]
    assert machine["agents"] == ["claude-code"]       # only the agents it can run
    assert "owner" not in machine and "key_id" not in machine

    r = await _queue(client, me, spec={**SPEC, "repo": "/etc"})
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["state"] == "queued" and job["spec"]["repo"] == "."   # its own repository
    assert job["created_by"] == "ada@acme.io"

    (offered,) = (await client.get("/v1/machines/m-laptop/jobs", headers=key)).json()["jobs"]
    assert offered["job_id"] == job["job_id"]
    claimed = await client.post(f"/v1/machines/m-laptop/jobs/{job['job_id']}/claim", headers=key)
    assert claimed.status_code == 200 and claimed.json()["state"] == "running"
    assert (await client.get("/v1/machines/m-laptop/jobs", headers=key)).json()["jobs"] == []

    p = await client.post(f"/v1/machines/m-laptop/jobs/{job['job_id']}/progress", headers=key,
                          json={"progress": {"done": 1, "total": 4, "solved": 1, "junk": 9}})
    assert p.json() == {"cancel_requested": False}
    seen = (await client.get(f"/v1/machine-jobs/{job['job_id']}", headers=me)).json()
    assert seen["progress"] == {"done": 1, "total": 4, "solved": 1}

    done = await client.post(f"/v1/machines/m-laptop/jobs/{job['job_id']}/complete",
                             headers=key, json={"state": "done", "eval_id": "se-abc"})
    assert done.status_code == 200, done.text
    seen = (await client.get(f"/v1/machine-jobs/{job['job_id']}", headers=me)).json()
    assert (seen["state"], seen["eval_id"]) == ("done", "se-abc")


async def test_nobody_runs_a_job_on_someone_elses_machine(
        client: httpx.AsyncClient, priv: Ed25519PrivateKey) -> None:  # noqa: F811
    """Queueing one would run a shell command of the queuer's choosing on the
    machine's owner's laptop."""
    ada, ada_key = await _developer(client, priv, "ada")
    bob, bob_key = await _developer(client, priv, "bob")
    await _machine(client, ada_key)
    r = await _queue(client, bob)
    assert r.status_code == 403
    assert (await client.get("/v1/machines", headers=bob)).json()["machines"][0]["yours"] is False

    job = (await _queue(client, ada)).json()
    # bob's key neither sees ada's machine's queue nor claims from it
    assert (await client.get("/v1/machines/m-laptop/jobs", headers=bob_key)).status_code == 404
    r = await client.post(f"/v1/machines/m-laptop/jobs/{job['job_id']}/claim", headers=bob_key)
    assert r.status_code == 404
    # nor takes the machine id over by heartbeating under it
    r = await client.post("/v1/machines/heartbeat", headers=bob_key,
                          json={"machine_id": "m-laptop", "host": "evil"})
    assert r.status_code == 409
    # nor cancels it
    assert (await client.post(f"/v1/machine-jobs/{job['job_id']}/cancel",
                              headers=bob)).status_code == 403
    # ada's machine still has it
    assert len((await client.get("/v1/machines/m-laptop/jobs",
                                 headers=ada_key)).json()["jobs"]) == 1


async def test_a_job_is_claimed_once(
        client: httpx.AsyncClient, priv: Ed25519PrivateKey) -> None:  # noqa: F811
    me, key = await _developer(client, priv, "ada")
    await _machine(client, key)
    job = (await _queue(client, me)).json()
    url = f"/v1/machines/m-laptop/jobs/{job['job_id']}/claim"
    assert (await client.post(url, headers=key)).status_code == 200
    assert (await client.post(url, headers=key)).status_code == 409


async def test_a_machine_that_goes_quiet_loses_the_job_and_it_is_not_rerun(
        client: httpx.AsyncClient, priv: Ed25519PrivateKey,  # noqa: F811
        monkeypatch: pytest.MonkeyPatch) -> None:
    from axor_backend.routers import machines_api

    me, key = await _developer(client, priv, "ada")
    await _machine(client, key)
    job = (await _queue(client, me)).json()
    monkeypatch.setattr(machines_api, "LEASE_SECONDS", -1)   # a lease already lapsed
    await client.post(f"/v1/machines/m-laptop/jobs/{job['job_id']}/claim", headers=key)
    seen = (await client.get(f"/v1/machine-jobs/{job['job_id']}", headers=me)).json()
    assert seen["state"] == "lost" and "stopped reporting" in seen["error"]
    assert (await client.get("/v1/machines/m-laptop/jobs", headers=key)).json()["jobs"] == []
    r = await client.post(f"/v1/machines/m-laptop/jobs/{job['job_id']}/progress", headers=key,
                          json={"progress": {"done": 1}})
    assert r.status_code == 409


async def test_cancel_stops_a_queued_job_now_and_a_running_one_between_runs(
        client: httpx.AsyncClient, priv: Ed25519PrivateKey) -> None:  # noqa: F811
    me, key = await _developer(client, priv, "ada")
    await _machine(client, key)
    queued = (await _queue(client, me)).json()
    r = await client.post(f"/v1/machine-jobs/{queued['job_id']}/cancel", headers=me)
    assert r.json()["state"] == "cancelled"
    claim = await client.post(f"/v1/machines/m-laptop/jobs/{queued['job_id']}/claim", headers=key)
    assert claim.status_code == 409

    running = (await _queue(client, me)).json()
    await client.post(f"/v1/machines/m-laptop/jobs/{running['job_id']}/claim", headers=key)
    r = await client.post(f"/v1/machine-jobs/{running['job_id']}/cancel", headers=me)
    assert r.json() == {"job_id": running["job_id"], "state": "running",
                        "stops": "after the run in progress"}
    p = await client.post(f"/v1/machines/m-laptop/jobs/{running['job_id']}/progress",
                          headers=key, json={"progress": {"done": 1}})
    assert p.json() == {"cancel_requested": True}
    done = await client.post(f"/v1/machines/m-laptop/jobs/{running['job_id']}/complete",
                             headers=key, json={"state": "cancelled"})
    assert done.status_code == 200


@pytest.mark.parametrize("spec,why", [
    ({**SPEC, "trials": 0}, "trials"),
    ({**SPEC, "trials": 51}, "trials"),
    ({**SPEC, "skills": []}, "skills"),
    ({**SPEC, "tasks": [{"prompt": "x"}]}, "check"),
    ({**SPEC, "agent": "rm -rf"}, "agent"),
])
async def test_a_spec_the_eval_cannot_run_is_refused(
        client: httpx.AsyncClient, priv: Ed25519PrivateKey,  # noqa: F811
        spec: dict, why: str) -> None:
    me, key = await _developer(client, priv, "ada")
    await _machine(client, key)
    r = await _queue(client, me, spec=spec)
    assert r.status_code == 400 and why in r.json()["detail"]
