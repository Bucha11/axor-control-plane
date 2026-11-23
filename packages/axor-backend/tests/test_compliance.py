"""The Enterprise rung's two: a period of the whole governed surface as one
document a reviewer can read and check, and a legal hold that stops the
workspace's own retention from deleting the evidence under it."""
from __future__ import annotations

import json
import pathlib
from collections.abc import AsyncIterator

import httpx
import pytest
from axor_backend import compliance
from axor_backend.app import create_app
from axor_backend.ee.license import License
from axor_backend.tenancy import PUBLIC_ORG


@pytest.fixture
async def client(tmp_path: pathlib.Path) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/c.db",
                     operator_keys={}, allow_unsigned=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cp.test") as c, \
            app.router.lifespan_context(app):
        app.state.licenses[PUBLIC_ORG] = License(PUBLIC_ORG, "enterprise", 50, "2999-01-01")
        c.app = app  # type: ignore[attr-defined]
        yield c


async def _session(c: httpx.AsyncClient, node: str, developer: str) -> None:
    await c.post(f"/v1/plane/{node}/profile", json={
        "kind": "coding", "agent": "claude-code", "developer": developer})
    await c.post(f"/v1/plane/{node}/telemetry", json={"run_id": f"{node}-hb", "events": [
        {"seq": 0, "kind": "heartbeat", "payload": {"applied_version": 0, "level": "NORMAL",
                                                    "budget_remaining": None}}]})


async def _some_history(c: httpx.AsyncClient) -> None:
    await _session(c, "claude-code-ada", "dev-ada")
    await c.post("/v1/plane/claude-code-ada/command",
                 json={"version": 1, "state": {"paused": True}})
    await c.post("/v1/plane/claude-code-ada/command",
                 json={"version": 2, "state": {"budget_cap_calls": 40}})
    await c.put("/v1/coding/brake", json={"on": True, "reason": "a skill gone bad"})
    await c.put("/v1/coding/brake", json={"on": False})
    await c.put("/v1/coding/rules", json={
        "allow": [], "mode": "enforce", "trusted_sources": ["docs.internal.dev"],
        "exceptions": [{"rule": "secrets", "path": "tests/fixtures/*", "note": "fake keys"}]})


async def test_the_pack_covers_the_period_and_says_where_each_part_came_from(
        client: httpx.AsyncClient) -> None:
    await _some_history(client)
    r = await client.get("/v1/audit/compliance", params={"from": "2020-01-01", "to": "2099-01-01"})
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith('attachment; filename="axor-compliance-')
    pack = r.json()
    assert pack["schema"] == "axor.compliance/1" and pack["plan"] == "enterprise"
    assert pack["period"] == {"from": "2020-01-01", "to": "2099-01-01"}
    # oversight: the interventions, by act and by person
    over = pack["oversight"]
    assert over["interventions"] == 4           # pause, budget, brake on, brake off
    assert set(over["by_act"]) == {"pause", "budget", "brake_on", "brake_off"}
    assert over["by_act"]["brake_on"] == 1
    assert {a["act"] for a in pack["policy_changes"]["acts"]} == {
        "exception_granted", "source_trusted"}
    assert pack["fleet"]["agents_seen"] == 1
    assert pack["fleet"]["developers_running_coding_agents"] == 1
    # every section names its source, so an empty one reads as "nothing was
    # recorded", not "we did not look"
    for name in ("fleet", "oversight", "attestations", "policy_changes",
                 "access_changes", "keys", "record_keeping"):
        assert pack[name]["source"]
    assert pack["attestations"]["count"] == 0   # none in this period, and it says so
    # what it answers, with the count of records behind each line
    articles = {c["control"]: c for c in pack["controls"]}
    assert "Art. 14 — Human oversight" in articles
    assert articles["Art. 14 — Human oversight"]["evidence"] == {
        "oversight": 4, "attestations": 0}    # counted in its own units, never summed
    # the digest is over the body with the digest itself removed, so a reviewer
    # recomputes it from the copy they hold
    assert pack["integrity"]["sha256"] == compliance.digest(pack)
    assert "signature" not in pack["integrity"]


async def test_the_readable_copy_is_the_same_pack(client: httpx.AsyncClient) -> None:
    await _some_history(client)
    r = await client.get("/v1/audit/compliance",
                         params={"from": "2020-01-01", "to": "2099-01-01", "format": "html"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    html = r.text
    assert "Governed-agent compliance pack" in html and "a skill gone bad" in html
    assert "Art. 14 — Human oversight" in html
    # self-contained: an air-gapped review room sees the same document
    assert "http://" not in html.replace("http://www.w3.org", "") and "<script" not in html
    # the digest is on the face of it, so the printed copy and the JSON one
    # can be told apart or matched by eye
    import re

    assert re.search(r"<code>[0-9a-f]{64}</code>", html)


async def test_two_generations_differ_only_in_when_and_by_whom(
        client: httpx.AsyncClient) -> None:
    """The digest covers the document, generation time included — so the same
    period asked for twice is two documents, alike in everything it reports."""
    await _some_history(client)
    period = {"from": "2020-01-01", "to": "2099-01-01"}
    one = (await client.get("/v1/audit/compliance", params=period)).json()
    two = (await client.get("/v1/audit/compliance", params=period)).json()
    assert one["integrity"]["sha256"] != two["integrity"]["sha256"]
    for pack in (one, two):
        for key in ("generated_on", "integrity"):
            pack.pop(key)
    assert one == two


async def test_the_coverage_line_says_when_the_trail_starts_late(
        client: httpx.AsyncClient) -> None:
    """A reviewer's first question is what is missing, so the document answers
    it without being asked."""
    await _some_history(client)
    pack = (await client.get("/v1/audit/compliance",
                             params={"from": "2019-01-01", "to": "2099-01-01"})).json()
    assert "starts at" in pack["record_keeping"]["coverage"]
    empty = (await client.get("/v1/audit/compliance",
                              params={"from": "2019-01-01", "to": "2019-02-01"})).json()
    assert "no act of any kind is recorded" in empty["record_keeping"]["coverage"]
    assert empty["oversight"]["interventions"] == 0


async def test_a_hold_outranks_the_workspaces_own_retention(client: httpx.AsyncClient) -> None:
    store = client.app.state.store  # type: ignore[attr-defined]
    old = {"ts": "2020-01-01T00:00:00+00:00", "action": "pause", "target": "x",
           "by": {"kind": "open", "who": "operator"}}
    await store.add_vault_audit("control", old, old["ts"])
    placed = await client.post("/v1/audit/holds", json={"reason": "Acme v. Roe, discovery"})
    assert placed.status_code == 201
    hold = placed.json()
    assert hold["hold_id"].startswith("hold_") and hold["released_ts"] is None
    # retention would have pruned that row; the hold stops it
    r = await client.put("/v1/audit/retention", json={"days": 90})
    assert r.status_code == 200 and r.json() == {"days": 90, "pruned": 0}
    assert "pause" in [i["action"] for i in (await client.get("/v1/audit")).json()["items"]]
    # placing and releasing are themselves on the trail
    acts = [i["action"] for i in (await client.get("/v1/audit")).json()["items"]]
    assert "hold_placed" in acts
    assert (await client.get("/v1/audit/holds")).json()["holds"][0]["reason"] \
        == "Acme v. Roe, discovery"
    released = await client.delete(f"/v1/audit/holds/{hold['hold_id']}")
    assert released.status_code == 200 and released.json()["released_ts"]
    assert (await client.delete(f"/v1/audit/holds/{hold['hold_id']}")).status_code == 404
    # released, it is kept on the record — that it existed is part of the story
    held = (await client.get("/v1/audit/holds")).json()["holds"]
    assert len(held) == 1 and held[0]["released_ts"]
    # and now retention bites
    assert (await client.put("/v1/audit/retention", json={"days": 90})).json()["pruned"] == 1
    # the pack carries the hold, so the period that was frozen says so
    pack = (await client.get("/v1/audit/compliance",
                             params={"from": "2020-01-01", "to": "2099-01-01"})).json()
    assert pack["record_keeping"]["legal_holds"][0]["hold_id"] == hold["hold_id"]


async def test_a_hold_needs_a_reason(client: httpx.AsyncClient) -> None:
    assert (await client.post("/v1/audit/holds", json={})).status_code == 400
    assert (await client.post("/v1/audit/holds", json={"reason": " x "})).status_code == 400


async def test_both_are_the_enterprise_rungs(client: httpx.AsyncClient) -> None:
    app = client.app  # type: ignore[attr-defined]
    app.state.licenses[PUBLIC_ORG] = License(PUBLIC_ORG, "security", 50, "2999-01-01")
    for call in (client.get("/v1/audit/compliance"), client.get("/v1/audit/holds"),
                 client.post("/v1/audit/holds", json={"reason": "discovery"})):
        r = await call
        assert r.status_code == 402 and r.json()["detail"]["plan"] == "enterprise"
    # the trail, its export and the monthly report stay Business
    assert (await client.get("/v1/audit/export")).status_code == 200


async def test_the_period_defaults_to_the_quarter_just_ended(client: httpx.AsyncClient) -> None:
    from datetime import UTC, datetime

    pack = (await client.get("/v1/audit/compliance")).json()
    frm, to = pack["period"]["from"], pack["period"]["to"]
    assert frm.endswith(("-01-01", "-04-01", "-07-01", "-10-01")) and frm < to
    assert to <= datetime.now(UTC).date().isoformat() or to.endswith("-01")
    assert (await client.get("/v1/audit/compliance",
                             params={"from": "2026-13-01"})).status_code == 400
    assert (await client.get("/v1/audit/compliance",
                             params={"from": "2026-05-01", "to": "2026-04-01"})).status_code == 400


def test_the_digest_covers_everything_but_itself() -> None:
    def pack(**over: object) -> dict:
        args = {"organization": "acme", "frm": "2026-01-01", "to": "2026-04-01",
                "generated_by": "olga", "tier": "enterprise", "licensed_to": "Acme",
                "rows": [], "attestations": [], "fleet": {}, "developers": [],
                "held": [], "retention_days": None, **over}
        return compliance.build(**args)  # type: ignore[arg-type]

    one = pack()
    assert one["integrity"]["sha256"] == compliance.digest(one)
    # a row added anywhere changes it
    two = pack(rows=[{"kind": "control", "ts": "2026-02-01T00:00:00Z", "action": "stop",
                      "who": "olga@acme.io", "target": "n1", "detail": {}}])
    assert two["integrity"]["sha256"] != one["integrity"]["sha256"]
    # and the signature does not feed back into the digest it signs
    signed = dict(two)
    signed["integrity"] = {**two["integrity"], "signature": "ab", "signed_by": "k1"}
    assert compliance.digest(signed) == compliance.digest(two)
    assert compliance.signable(signed) == two["integrity"]["sha256"].encode()


async def test_the_pack_can_be_signed_through_the_vaults_custody(
        client: httpx.AsyncClient) -> None:
    """A signature is custody's to give: the compliance pack asks for one
    through the same wall, it does not become a way around it."""
    from nacl.signing import VerifyKey

    await _some_history(client)
    made = await client.post("/v1/vault/signing/keys",
                             json={"key_id": "audit-2026", "operators": ["operator"]})
    assert made.status_code in (200, 201), made.text
    public = made.json()["public_key_hex"]
    r = await client.get("/v1/audit/compliance",
                         params={"from": "2020-01-01", "to": "2099-01-01", "sign": "audit-2026"})
    assert r.status_code == 200
    pack = r.json()
    assert pack["integrity"]["signed_by"] == "audit-2026"
    VerifyKey(bytes.fromhex(public)).verify(
        compliance.digest(pack).encode(), bytes.fromhex(pack["integrity"]["signature"]))
    # an unknown key is refused, and the refusal is in custody's own log
    bad = await client.get("/v1/audit/compliance",
                           params={"from": "2020-01-01", "to": "2099-01-01", "sign": "nope"})
    assert bad.status_code == 403
    assert json.dumps((await client.get("/v1/vault/signing/audit")).json()).count("nope") >= 1


def test_a_period_too_big_for_one_pack_says_so() -> None:
    """The pack carries a bounded number of acts. A period past it is cut, and
    a cut it did not mention would be a document claiming to be complete."""
    pack = compliance.build(
        organization="acme", frm="2026-01-01", to="2026-04-01", generated_by="olga",
        tier="enterprise", licensed_to="Acme", rows=[], attestations=[], fleet={},
        developers=[], held=[], retention_days=None, truncated_at=100_000)
    assert "cut there" in pack["record_keeping"]["coverage"]
    assert "100,000" in pack["record_keeping"]["coverage"]
