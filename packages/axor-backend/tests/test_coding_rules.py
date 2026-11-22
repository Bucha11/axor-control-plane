"""The team's rules for coding agents: decided once by an admin, fetched by
every sidecar with the skills policy. Until an admin saves them, the sidecars
keep their own defaults (and each repository's .axor/rules.json)."""
from __future__ import annotations

import httpx
from test_identity_auth import _bearer, _mint, client, priv  # noqa: F401


async def test_the_admin_decides_and_every_sidecar_fetches_it(
        client: httpx.AsyncClient, priv) -> None:  # noqa: ANN001, F811
    owner = _bearer(_mint(priv, role="owner"))
    member = _bearer(_mint(priv, role="member", sub="usr_dev2", eml="dev2@acme.io"))
    screen = (await client.get("/v1/coding/rules", headers=owner)).json()
    assert screen["set"] is False and screen["mode"] == "enforce"
    ids = {r["id"] for r in screen["catalog"]}
    assert {"secrets", "sends_out", "download_and_run"} <= ids
    # nothing saved: the sidecars keep their own defaults
    assert "rules" not in (await client.get("/v1/coding/policy", headers=member)).json()

    # a member steers sessions, but what every agent may do is the admin's
    r = await client.put("/v1/coding/rules", headers=member, json={"allow": ["secrets"]})
    assert r.status_code == 403
    r = await client.put("/v1/coding/rules", headers=owner,
                         json={"allow": ["destructive"], "mode": "observe"})
    assert r.status_code == 200 and r.json()["by"]
    policy = (await client.get("/v1/coding/policy", headers=member)).json()
    assert policy["rules"] == {"allow": ["destructive"], "mode": "observe"}


async def test_nonsense_and_the_agents_own_guard_are_refused(
        client: httpx.AsyncClient, priv) -> None:  # noqa: ANN001, F811
    owner = _bearer(_mint(priv, role="owner"))
    assert (await client.put("/v1/coding/rules", headers=owner,
                             json={"allow": ["teleport"]})).status_code == 400
    assert (await client.put("/v1/coding/rules", headers=owner,
                             json={"allow": [], "mode": "loose"})).status_code == 400
