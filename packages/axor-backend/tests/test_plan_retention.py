"""Run history by plan on the hosted plane: one rung up keeps more of it.

Free keeps a week, Coding and Business a month, Team 90 days, Security a year,
Enterprise as long as the workspace chooses. A self-hosted plane keeps its own
AXOR_RETENTION_DAYS: what it stores is the operator's.
"""
from __future__ import annotations

import pathlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from axor_backend.app import create_app
from axor_backend.lifecycle import prune_once, retention_days
from axor_backend.tenancy import PUBLIC_ORG, set_current_org


def _state(hosted: bool, days: float | None, tier: str | None) -> SimpleNamespace:
    lic = SimpleNamespace(workspace_tier=tier) if tier else None
    return SimpleNamespace(config=SimpleNamespace(hosted=hosted, retention_days=days),
                           licenses={"org_a": lic} if lic else {})


@pytest.mark.parametrize(("tier", "days"), [
    (None, 7), ("community", 7), ("coding", 30), ("business", 30), ("team", 90),
    ("security", 365), ("enterprise", None)])
def test_each_plan_keeps_its_window_on_the_hosted_plane(tier: str | None, days: int | None) -> None:
    assert retention_days(_state(True, None, tier), "org_a") == days


def test_the_deployments_window_is_a_ceiling_and_self_hosted_keeps_its_own() -> None:
    assert retention_days(_state(True, 14, "security"), "org_a") == 14
    assert retention_days(_state(True, 14, "enterprise"), "org_a") == 14
    assert retention_days(_state(False, None, "community"), "org_a") is None
    assert retention_days(_state(False, 400, "community"), "org_a") == 400


def _ago(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


async def _runs(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, tier: str | None,
                hold: bool = False) -> list[str]:
    monkeypatch.setenv("AXOR_HOSTED", "1")
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}-{tier}-{hold}.db",
                     operator_keys={}, allow_unsigned=True)
    async with app.router.lifespan_context(app):
        set_current_org(PUBLIC_ORG)
        store = app.state.store
        await store.upsert_run("run_40d", "n", "s", _ago(40))
        await store.upsert_run("run_10d", "n", "s", _ago(10))
        await store.upsert_run("run_2d", "n", "s", _ago(2))
        if tier:
            app.state.licenses[PUBLIC_ORG] = SimpleNamespace(workspace_tier=tier)
        if hold:
            from axor_backend import compliance
            await store.set_setting(compliance.HOLDS_SETTING,
                                    [{"id": "h1", "reason": "dispute", "opened_ts": _ago(1)}])
        await prune_once(app.state)
        set_current_org(PUBLIC_ORG)
        return sorted(r["run_id"] for r in await store.list_runs())


async def test_the_sweep_keeps_what_the_plan_pays_for(
        tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert await _runs(tmp_path, monkeypatch, None) == ["run_2d"]
    assert await _runs(tmp_path, monkeypatch, "coding") == ["run_10d", "run_2d"]
    assert await _runs(tmp_path, monkeypatch, "team") == ["run_10d", "run_2d", "run_40d"]


async def test_a_legal_hold_keeps_every_run(
        tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert await _runs(tmp_path, monkeypatch, None, hold=True) == ["run_10d", "run_2d", "run_40d"]
