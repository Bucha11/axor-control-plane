"""The invoice is drawn on the calendar the meter is written in.

`clock.today` states the rule and the reason: usage is metered per UTC day
"so that a fleet spanning time zones is counted once, on one calendar, and a
customer and the vendor reading the same invoice see the same days".
`plane.record_node_activity` stamps every row with it.

The two billing routes computed their window from `date.today()` — the HOST's
local day:

    TZ=UTC                date.today()=2026-09-13  clock.today()=2026-09-13
    TZ=Pacific/Auckland   date.today()=2026-09-14  clock.today()=2026-09-13
    TZ=Asia/Kolkata       date.today()=2026-09-14  clock.today()=2026-09-13

So the window and the rows it selects were on different calendars for part of
every day on any deployment east or west of UTC — and at a month boundary it
moved the DEFAULT INVOICE by a month:

    TZ=Pacific/Auckland   at 2026-03-01T00:30:00+13:00
       local date 2026-03-01 -> /invoice defaults to 2026-02
       UTC   date 2026-02-28 -> it should default to  2026-01

which is the one number a customer is asked to pay.

Asserted against a FROZEN UTC day rather than against a timezone, so the test
says the same thing on every machine: freeze the meter's clock to a date the
host's own calendar cannot be on, and the route must follow the meter.
"""
from __future__ import annotations

import pathlib
from datetime import date, timedelta

import httpx
import pytest
from axor_backend.app import create_app
from axor_backend.ee.license import sign_license

TOKEN = "t"
H = {"Authorization": f"Bearer {TOKEN}"}
# The last day of a month, in a year no machine running this suite is in. Any
# route reading the host's clock instead of the meter's lands somewhere else.
FROZEN = "2020-02-29"


@pytest.fixture
def vendor(monkeypatch: pytest.MonkeyPatch) -> str:
    from nacl.signing import SigningKey

    key = SigningKey.generate()
    monkeypatch.setenv("AXOR_VENDOR_PUBKEY", key.verify_key.encode().hex())
    return sign_license(
        {"organization": "T", "workspace_tier": "team",
         "governed_node_ceiling": 10, "self_hosted_runner": False,
         "expires_at": "2999-01-01", "features": []},
        bytes(key).hex(),
    )


@pytest.fixture
async def client(
    tmp_path: pathlib.Path, vendor: str, monkeypatch: pytest.MonkeyPatch,
) -> httpx.AsyncClient:
    app = create_app(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/l.db",
        operator_keys={}, allow_unsigned=True, api_token=TOKEN,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://backend.test",
        timeout=60,
    ) as c, app.router.lifespan_context(app):
        r = await c.post("/v1/license/verify",
                         json={"license_json": vendor}, headers=H)
        assert r.status_code == 200, r.text
        monkeypatch.setattr(
            "axor_backend.routers.license_api.today", lambda: FROZEN)
        yield c


class TestTheBillingRoutesFollowTheMeter:
    async def test_the_invoice_defaults_to_the_month_the_meter_just_closed(
        self, client: httpx.AsyncClient,
    ) -> None:
        """The meter's day is the last of February, so the month just ended is
        January — whatever month the host believes it is in."""
        body = (await client.get("/v1/license/invoice", headers=H)).json()
        assert body["month"] == "2020-01", body

    async def test_the_usage_window_starts_from_the_meters_month(
        self, client: httpx.AsyncClient,
    ) -> None:
        body = (await client.get("/v1/license/usage?months=3", headers=H)).json()
        assert [m["month"] for m in body["months"]] == [
            "2020-02", "2020-01", "2019-12"], body

    async def test_it_does_not_follow_the_host_clock(
        self, client: httpx.AsyncClient,
    ) -> None:
        """The property stated directly: no answer here names the host's month.

        This is the assertion that fails on the old code on ANY machine, rather
        than only on one east of UTC on the right day.
        """
        host_month = date.today().strftime("%Y-%m")
        # The invoice default is "the month just ended", so comparing it to the
        # host's CURRENT month can never fail — the host's PREVIOUS month is
        # what the old code would have answered.
        host_previous = (
            date.today().replace(day=1) - timedelta(days=1)
        ).strftime("%Y-%m")
        invoice = (await client.get("/v1/license/invoice", headers=H)).json()
        usage = (await client.get("/v1/license/usage?months=3", headers=H)).json()
        assert invoice["month"] != host_previous
        assert host_month not in {m["month"] for m in usage["months"]}

    async def test_an_explicit_month_is_still_honoured(
        self, client: httpx.AsyncClient,
    ) -> None:
        """The default is what the calendar decides; a named month is the
        caller's and is not second-guessed."""
        body = (await client.get(
            "/v1/license/invoice?month=2019-07", headers=H)).json()
        assert body["month"] == "2019-07"


class TestNothingElseReadsTheHostClock:
    def test_the_backend_takes_its_calendar_from_one_place(self) -> None:
        """These two were the only local-clock calls in the backend and the
        proxy, and they were the two that decide what a customer is billed."""
        import subprocess

        root = pathlib.Path(__file__).resolve().parents[3]
        found = subprocess.run(
            ["grep", "-rn", r"date\.today()\|datetime\.now()\|datetime\.today()",
             "--include=*.py",
             str(root / "packages/axor-backend/src"),
             str(root / "packages/axor-proxy/src")],
            capture_output=True, text=True, check=False,
        ).stdout
        offenders = [
            line for line in found.splitlines()
            if "datetime.now(UTC)" not in line and not line.split(":", 2)[2].strip().startswith("#")
        ]
        assert offenders == [], "\n".join(offenders)
