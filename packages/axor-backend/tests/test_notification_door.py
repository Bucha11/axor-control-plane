"""The subscribe door, typed — and a permanent mute that came back as a number.

`a6d8b56` fixed a debounce that was a permanent mute: the clock it measured
against never advanced, so any debounce suppressed everything after the first
event. The clock is real now. The FIELD was never checked, and `float()` accepts
everything `json.loads` produces — including the `Infinity` literal, which
Python's JSON reader takes by default:

    debounce_seconds: Infinity   -> 200 {"subscribed": "https://s.test/inf", ...}
    debounce_seconds: 1e308      -> 200 {"subscribed": "https://s.test/big", ...}

    https://s.test/inf   debounce=inf      fires 1/5 events one second apart
    https://s.test/big   debounce=1e+308   fires 1/5 events one second apart

`Subscription.due` reads `(now - last) < inf` as true for every later event, so
the subscription fires once, ever, and the row says it is live. That is the same
outcome the earlier fix removed, reached through the door instead of the clock.
`NaN` went further and reached the INSERT, where SQLite refuses it as a NOT NULL
violation — the caller's number returned as our 500.

And the rest of the body was untyped in the way the rest of this audit keeps
finding: a dict of triggers subscribed to its KEYS and answered 200, a bare
string subscribed to its letters, a nested list and a non-string url each left
the route as a 500.
"""
from __future__ import annotations

import pathlib
import time

import httpx
import pytest
from axor_backend.app import create_app
from axor_backend.limits import MAX_DEBOUNCE_SECONDS
from axor_backend.notifications import TRIGGERS

TOKEN = "t"
H = {"Authorization": f"Bearer {TOKEN}", "content-type": "application/json"}
TRIGGER = sorted(TRIGGERS)[0]


@pytest.fixture
async def client(tmp_path: pathlib.Path) -> httpx.AsyncClient:
    app = create_app(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/n.db",
        operator_keys={}, allow_unsigned=True, api_token=TOKEN,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://backend.test",
        timeout=60,
    ) as c, app.router.lifespan_context(app):
        c._app = app  # type: ignore[attr-defined]
        yield c


async def subscribe(client: httpx.AsyncClient, **body: object) -> httpx.Response:
    return await client.post("/v1/notifications/subscribe", json=body, headers=H)


class TestADebounceCannotBeAMute:
    @pytest.mark.parametrize("literal", ["Infinity", "-Infinity", "NaN"])
    async def test_a_non_finite_debounce_is_refused(
        self, client: httpx.AsyncClient, literal: str,
    ) -> None:
        """These arrive as real numbers: `json.loads` accepts the literals, so
        an HTTP client that writes them raw gets them past the parser."""
        raw = (
            f'{{"url": "https://sink.test/x", "triggers": ["{TRIGGER}"], '
            f'"debounce_seconds": {literal}}}'
        ).encode()
        r = await client.post("/v1/notifications/subscribe", content=raw, headers=H)
        assert r.status_code == 400, r.text
        assert r.json()["detail"] == "debounce_seconds must be a finite number"
        assert (await client.get(
            "/v1/notifications/subscriptions", headers=H)).json() == []

    async def test_a_debounce_past_the_bound_is_refused(
        self, client: httpx.AsyncClient,
    ) -> None:
        r = await subscribe(client, url="https://sink.test/x", triggers=[TRIGGER],
                            debounce_seconds=MAX_DEBOUNCE_SECONDS + 1)
        assert r.status_code == 400
        assert f"at most {MAX_DEBOUNCE_SECONDS}" in r.json()["detail"]
        assert "muted" in r.json()["detail"]

    async def test_the_bound_itself_is_accepted(
        self, client: httpx.AsyncClient,
    ) -> None:
        assert (await subscribe(
            client, url="https://sink.test/x", triggers=[TRIGGER],
            debounce_seconds=MAX_DEBOUNCE_SECONDS)).status_code == 200

    async def test_the_largest_accepted_debounce_still_ends(
        self, client: httpx.AsyncClient,
    ) -> None:
        """What the bound is FOR, read off `due()` rather than off the number.

        An earlier draft of this test stored a two-second debounce and asserted
        that five events a second apart did not collapse to one — which is true
        of two seconds whatever the door does, and no mutation of the door could
        make it fail. What the door actually guarantees is that every debounce
        it stores is one that ENDS: the window elapses and the subscription
        speaks again. `inf` was the counterexample, and it answered 200.
        """
        await subscribe(client, url="https://sink.test/slow", triggers=[TRIGGER],
                        debounce_seconds=MAX_DEBOUNCE_SECONDS)
        notifier = client._app.state.notifier  # type: ignore[attr-defined]
        sub = next(s for s in notifier._subs if s.url == "https://sink.test/slow")
        start = time.time()
        assert sub.due(TRIGGER, "n0", start) is True
        assert sub.due(TRIGGER, "n0", start + MAX_DEBOUNCE_SECONDS - 1) is False
        assert sub.due(TRIGGER, "n0", start + MAX_DEBOUNCE_SECONDS + 1) is True

    async def test_a_negative_debounce_is_refused(
        self, client: httpx.AsyncClient,
    ) -> None:
        """It behaved as "no debounce", which is a reasonable reading of a
        number nobody meant to send."""
        r = await subscribe(client, url="https://sink.test/x", triggers=[TRIGGER],
                            debounce_seconds=-5)
        assert r.status_code == 400
        assert r.json()["detail"] == "debounce_seconds must not be negative"

    async def test_true_is_not_a_debounce(
        self, client: httpx.AsyncClient,
    ) -> None:
        """`bool` is an `int` in Python, so this stored a one-second debounce."""
        r = await subscribe(client, url="https://sink.test/x", triggers=[TRIGGER],
                            debounce_seconds=True)
        assert r.status_code == 400
        assert r.json()["detail"] == "debounce_seconds must be a number"


class TestTheBodyIsTyped:
    @pytest.mark.parametrize(("body", "detail"), [
        ({"url": "https://s.test/a", "triggers": TRIGGER},
         "triggers must be a list of trigger names"),
        ({"url": "https://s.test/a", "triggers": {TRIGGER: 1}},
         "triggers must be a list of trigger names"),
        ({"url": "https://s.test/a", "triggers": [[TRIGGER]]},
         "triggers must be a list of trigger names"),
        ({"url": "https://s.test/a", "triggers": [TRIGGER, None]},
         "triggers must be a list of trigger names"),
        ({"url": "https://s.test/a", "triggers": []},
         "triggers must be non-empty"),
        ({"url": {"a": 1}, "triggers": [TRIGGER]},
         "url must be a non-empty string"),
        ({"url": 42, "triggers": [TRIGGER]},
         "url must be a non-empty string"),
        ({"url": "", "triggers": [TRIGGER]},
         "url must be a non-empty string"),
        ({"url": "https://s.test/a", "triggers": [TRIGGER], "label": {"a": 1}},
         "label must be a non-empty string"),
        ({"url": "https://s.test/a", "triggers": [TRIGGER],
          "node_pattern": ["n*"]}, "node_pattern must be a non-empty string"),
    ])
    async def test_a_bad_body_is_the_callers_fault(
        self, client: httpx.AsyncClient, body: dict, detail: str,
    ) -> None:
        r = await subscribe(client, **body)
        assert r.status_code == 400, r.text
        assert r.json()["detail"] == detail

    async def test_nothing_is_stored_for_a_refused_body(
        self, client: httpx.AsyncClient,
    ) -> None:
        """The dict of triggers used to answer 200 and store a real
        subscription, keyed on `set(dict)` — its KEYS."""
        await subscribe(client, url="https://s.test/a", triggers={TRIGGER: 1})
        assert (await client.get(
            "/v1/notifications/subscriptions", headers=H)).json() == []

    async def test_the_unknown_trigger_check_is_unchanged(
        self, client: httpx.AsyncClient,
    ) -> None:
        """Names are still checked against the vocabulary; this only made sure
        they ARE names first."""
        r = await subscribe(client, url="https://s.test/a", triggers=["nope"])
        assert r.status_code == 400
        assert "unknown triggers: ['nope']" in r.json()["detail"]

    async def test_unsubscribe_types_its_url_too(
        self, client: httpx.AsyncClient,
    ) -> None:
        r = await client.post("/v1/notifications/unsubscribe",
                              json={"url": {"a": 1}}, headers=H)
        assert r.status_code == 400
        assert r.json()["detail"] == "url must be a non-empty string"

    async def test_a_plain_subscription_still_works(
        self, client: httpx.AsyncClient,
    ) -> None:
        r = await subscribe(client, url="https://sink.test/ok",
                            triggers=[TRIGGER], debounce_seconds=30)
        assert r.status_code == 200
        assert r.json()["triggers"] == [TRIGGER]
        subs = (await client.get(
            "/v1/notifications/subscriptions", headers=H)).json()
        assert [s["url"] for s in subs] == ["https://sink.test/ok"]
        assert subs[0]["debounce_seconds"] == 30.0
