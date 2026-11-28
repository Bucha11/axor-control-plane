"""QA-197: Safe to ship? never passes normal work the rules would now refuse.

A proxy run is recorded observe-only: its calls carry no verdict. Kept as
normal work, the check skipped them, so a run calling `web_search` under rules
that refuse it read "still passes · every step goes as it did".
"""
from __future__ import annotations

import pathlib

import httpx
import pytest
from axor_backend.app import create_app

RULES = {"version": "axor-config/1", "default": "DENY",
         "sinks": {t: {"consequence_class": "READ"} for t in ("bash", "ticket_read")}}


def _line(seq: int, kind: str, **payload: object) -> dict:
    return {"schema_version": "1.0", "seq": seq, "node_id": "proxy", "kind": kind,
            "ts": f"2026-11-27T19:00:0{seq}+00:00", "causal_root": None, "gate": None,
            "verdict": None, "payload": payload}


@pytest.mark.parametrize("would_deny, result, still", [
    (False, "regressed", 0),   # ran, and these rules would refuse it now
    (True, "passed", 1),       # refused by the rules of the day too: a known refusal
])
async def test_qa197_an_ungoverned_call_the_rules_refuse_is_not_a_pass(
        tmp_path: pathlib.Path, would_deny: bool, result: str, still: int) -> None:
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/r.db",
                     operator_keys={}, allow_unsigned=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cp.test") as c, app.router.lifespan_context(app):
        call = _line(0, "tool_call", tool="web_search", method="GET",
                     **({"would_deny": {"reason": "not allowed", "category": "capability"}}
                        if would_deny else {}))
        assert (await c.post("/v1/ingest/run_proxy", json={
            "node_id": "proxy", "scenario": "live",
            "events": [call, _line(1, "tool_result", tool="web_search", status=200)],
        })).status_code == 202
        assert (await c.post("/v1/pins/run_proxy",
                             json={"side": "must_pass", "label": "normal"})).status_code == 200
        report = (await c.post("/v1/regression", json={"config": RULES})).json()
        row = report["rows"][0]
        assert row["result"] == result, row
        assert len(row["still_refused"]) == still
        if not would_deny:
            assert row["new_denial"]["seq"] == 0 and "web_search" in row["new_denial"]["reason"]
            assert report["regressed"] == 1 and report["safe_to_ship"] is False
