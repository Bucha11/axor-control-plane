"""Two meters: production agents as governed nodes, coding agents by developer."""
from __future__ import annotations

import pathlib
from collections.abc import AsyncIterator
from datetime import date

import httpx
import pytest
from axor_backend.app import create_app
from axor_backend.ee.license import TIER_DEVELOPER_SEATS
from axor_backend.ee.pricing import statement

TOKEN = "master-secret-xyz"


@pytest.fixture
async def client(tmp_path: pathlib.Path) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/d.db",
                     operator_keys={}, allow_unsigned=True, api_token=TOKEN)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cp.test",
                                 headers={"Authorization": f"Bearer {TOKEN}"}) as c, \
            app.router.lifespan_context(app):
        yield c


async def _report(client: httpx.AsyncClient, node: str) -> None:
    r = await client.post(f"/v1/plane/{node}/telemetry", json={
        "run_id": f"{node}-hb", "scenario": "live",
        "events": [{"seq": 0, "kind": "heartbeat",
                    "payload": {"applied_version": 0, "level": "NORMAL",
                                "budget_remaining": None}}]})
    assert r.status_code == 202, r.text


async def _profile(client: httpx.AsyncClient, node: str, **body: str) -> int:
    return (await client.post(f"/v1/plane/{node}/profile", json=body)).status_code


async def test_coding_sessions_are_developers_not_governed_nodes(client) -> None:  # noqa: ANN001
    await _report(client, "prod-agent-1")
    for i in range(6):  # one developer, six sessions, two agents
        node = f"claude-code-host-{i}"
        await _report(client, node)
        assert await _profile(client, node, kind="coding", agent="claude-code",
                              developer="dev-aaa") == 204
    await _report(client, "codex-host-x")
    await _profile(client, "codex-host-x", kind="coding", agent="codex", developer="dev-bbb")
    await _report(client, "tree-1234")
    await _profile(client, "tree-1234", kind="tree")

    month = (await client.get("/v1/license/usage?months=1")).json()["months"][0]
    assert month["peak_nodes"] == 1          # only the production agent
    assert month["active_developers"] == 2   # two people, eight non-fleet nodes


async def test_the_first_profile_stands(client) -> None:  # noqa: ANN001
    await _report(client, "n1")
    await _profile(client, "n1", kind="agent")
    # a node cannot later talk its way out of the fleet count
    await _profile(client, "n1", kind="coding", agent="codex", developer="dev-z")
    month = (await client.get("/v1/license/usage?months=1")).json()["months"][0]
    assert month["peak_nodes"] == 1 and month["active_developers"] == 0


async def test_bad_profiles_are_refused(client) -> None:  # noqa: ANN001
    assert await _profile(client, "x", kind="robot") == 400
    assert await _profile(client, "x", kind="coding", agent="codex") == 400  # no developer
    assert await _profile(client, "x", kind="coding", developer="a@b.com") == 400  # not opaque


def test_seats_are_billed_past_the_rung_s_allowance() -> None:
    on = date(2026, 12, 15)
    team = statement("2026-10", "org", "team", 10, 4, "2026-10-03", on=on,
                     included_developers=TIER_DEVELOPER_SEATS["team"], active_developers=8)
    assert team.billable_developers == 3 and team.seat_cents == 75_00
    assert team.total_cents == 299_00 + 75_00
    coding = statement("2026-10", "org", "coding", 1, 1, None, on=on,
                       included_developers=TIER_DEVELOPER_SEATS["coding"], active_developers=4)
    assert coding.total_cents == 100_00 and coding.base_cents == 0
    free = statement("2026-10", "org", "community", 1, 1, None, on=on,
                     included_developers=TIER_DEVELOPER_SEATS["community"], active_developers=1)
    assert free.total_cents == 0
    assert statement("2026-10", "org", "team", 10, 4, None, on=on).seat_cents == 0
