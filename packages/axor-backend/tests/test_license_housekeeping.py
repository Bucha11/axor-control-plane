"""The entitlement sweep: that it runs at all, and that one tenant cannot stop it.

Three things live in it — renewal, the expiry notice and the over-ceiling
warning — and all three were reached only through `retention_loop`, which the
lifespan starts only when `AXOR_RETENTION_DAYS` is set. The default is unset, so
on an ordinary deployment none of them ran, while the tests called
`license_sweep_once` directly and stayed green.
"""
from __future__ import annotations

import asyncio
import pathlib
from typing import Any

import pytest
from axor_backend.app import create_app
from axor_backend.lifecycle import license_sweep_once


async def _running_loops(**config: object) -> set[str]:
    app = create_app(operator_keys={}, allow_unsigned=True, **config)
    before = {t.get_coro().__qualname__ for t in asyncio.all_tasks()}
    async with app.router.lifespan_context(app):
        await asyncio.sleep(0)
        return {
            t.get_coro().__qualname__
            for t in asyncio.all_tasks()
            if t.get_coro().__qualname__ not in before
        }


class TestTheSweepDoesNotDependOnRetention:
    async def test_it_runs_with_no_retention_window_configured(
        self, tmp_path: pathlib.Path,
    ) -> None:
        loops = await _running_loops(
            database_url=f"sqlite+aiosqlite:///{tmp_path}/a.db",
        )
        assert "license_loop" in loops
        assert "retention_loop" not in loops  # unset means keep forever

    async def test_retention_still_has_its_own(
        self, tmp_path: pathlib.Path,
    ) -> None:
        loops = await _running_loops(
            database_url=f"sqlite+aiosqlite:///{tmp_path}/b.db",
            retention_days=30.0,
        )
        assert {"license_loop", "retention_loop"} <= loops


class _Lic:
    organization = "acme"
    expires_at = "2099-01-01"
    workspace_tier = "team"
    governed_node_ceiling = 5

    def is_expired(self, today: str) -> bool:
        return False

    def allows_nodes(self, count: int) -> bool:
        return count <= self.governed_node_ceiling


class TestOneTenantsFailureDoesNotSilenceTheRest:
    """`renew_once` was the only one of the three the sweep did not guard, and
    its docstring says it never raises. `usage_report` sat outside its own try,
    so a database hiccup made a liar of it — and the loop's outer suppress ate
    the exception, skipping every later tenant's notice and warning."""

    async def test_renew_once_survives_a_failing_meter(self) -> None:
        from axor_backend.licensing import renew_once

        class Store:
            async def node_usage(self, *a: object, **kw: object) -> dict:
                raise RuntimeError("db is down")

        class State:
            class config:
                usage_reporting = True
                license_renewal_url = "https://vendor.test/renew"
                vendor_pubkey = "00" * 32
                org = "acme"

            store = Store()
            licenses = {"acme": _Lic()}

        assert await renew_once(State(), "acme") is False

    async def test_a_later_tenant_is_still_swept(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        app = create_app(
            database_url=f"sqlite+aiosqlite:///{tmp_path}/c.db",
            operator_keys={}, allow_unsigned=True,
        )
        async with app.router.lifespan_context(app):
            state = app.state
            state.licenses = {"public": _Lic(), "org_b": _Lic()}
            swept: list[str] = []

            async def orgs() -> list[str]:
                return ["public", "org_b"]

            async def boom(_state: object, org: str) -> bool:
                raise RuntimeError(f"renewal exploded for {org}")

            async def note(_state: object, org: str) -> None:
                swept.append(org)

            monkeypatch.setattr(state.store, "list_orgs", orgs)
            monkeypatch.setattr(
                "axor_backend.licensing.renewal_due",
                lambda *a, **kw: _true(),
            )
            monkeypatch.setattr("axor_backend.licensing.renew_once", boom)
            monkeypatch.setattr("axor_backend.licensing.notify_expiring", note)

            await license_sweep_once(state)
            assert swept == ["public", "org_b"]


class TestARenewalThatCannotBeStoredIsNotARenewal:
    async def test_an_unstorable_license_is_not_installed_in_memory(self) -> None:
        """The store write used to sit outside any guard, and the in-memory
        install followed it. An entitlement only this process knows about is
        one the next restart forgets — worse than not having renewed, because
        nothing says so."""
        from axor_backend import licensing

        class Store:
            async def set_setting(self, *a: object, **kw: object) -> None:
                raise RuntimeError("disk is full")

        class State:
            class config:
                usage_reporting = False
                license_renewal_url = "https://vendor.test/renew"
                vendor_pubkey = "00" * 32
                org = "acme"

            store = Store()
            licenses: dict[str, Any] = {}

        state = State()
        new = _Lic()

        async def fetch(*a: object, **kw: object) -> str:
            return "{}"

        def verified(raw: str, pubkey: str) -> object:
            return new

        import pytest as _pytest

        with _pytest.MonkeyPatch().context() as m:
            m.setattr(licensing, "verify_license_str", verified)
            assert await licensing.renew_once(state, "acme", fetch=fetch) is False
        assert "acme" not in state.licenses


class TestThereIsOneWallClock:
    """`clock.py` exists because three modules had grown a private `_now`, and
    `licensing` had grown a fourth `today()` — the same body, the same
    semantics, in the file that decides when a license expires."""

    def test_no_module_defines_its_own_now_or_today(self) -> None:
        src = pathlib.Path(
            __file__
        ).resolve().parents[1] / "src" / "axor_backend"
        import ast

        offenders: list[str] = []
        for path in sorted(src.rglob("*.py")):
            if path.name == "clock.py":
                continue
            tree = ast.parse(path.read_text("utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name in {"now", "today"}:
                    offenders.append(f"{path.relative_to(src)}:{node.lineno} {node.name}")
        assert not offenders, (
            f"these shadow axor_backend.clock: {offenders}. One definition is "
            f"what keeps two timestamp formats out of one table."
        )


async def _true() -> bool:
    return True


class TestTheRenewalUrlIsWarnedAboutWhenItIsNotHttps:
    """The renewal request carries the organization and, with reporting on, the
    node counts. The *reply* is signature-checked, so the risk is one-way and
    easy to miss: what leaves, not what arrives."""

    @staticmethod
    def _warnings(url: str, caplog: pytest.LogCaptureFixture) -> list[str]:
        from axor_backend.config import AppConfig
        from axor_backend.lifecycle import warn_about_open_posture

        config = AppConfig(
            api_token="t", operator_keys={"k": "v"}, license_renewal_url=url,
        )
        with caplog.at_level("WARNING"):
            warn_about_open_posture(config)
        return [r.getMessage() for r in caplog.records]

    def test_plain_http_is_called_out(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        said = self._warnings("http://vendor.test/renew", caplog)
        assert any("LICENSE RENEWAL OVER HTTP " in m for m in said), said

    def test_a_url_with_no_scheme_at_all_is_called_out(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        said = self._warnings("vendor.test/renew", caplog)
        assert any("NO SCHEME AT ALL" in m for m in said), said

    def test_https_is_silent(self, caplog: pytest.LogCaptureFixture) -> None:
        said = self._warnings("https://vendor.test/renew", caplog)
        assert not any("LICENSE RENEWAL" in m for m in said), said

    def test_unset_is_silent(self, caplog: pytest.LogCaptureFixture) -> None:
        said = self._warnings("", caplog)
        assert not any("LICENSE RENEWAL" in m for m in said), said
