"""The regression corpus's two claims, and the config that feeds them.

`safe_to_ship` is the strongest sentence this product says. It is worth a file
of its own because both halves of it used to be looser than they read: the
must_block side asked whether the candidate config denies *anything* in the
trace, and the config parser coerced whatever it was handed into something the
kernel would accept.
"""
from __future__ import annotations

import json
import pathlib

import httpx
import pytest
from axor_backend.app import create_app
from axor_backend.errors import ConfigInvalid
from axor_backend.replay_api import kernel_config_from_json, parse_trace, regression_row
from axor_core.kernel.events import SCHEMA_VERSION


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


def _call(seq: int, tool: str, verdict: str, node: str = "n0") -> dict:
    return {
        "schema_version": SCHEMA_VERSION, "seq": seq, "node_id": node,
        "kind": "tool_call", "ts": "t", "causal_root": None, "gate": None,
        "verdict": verdict,
        "payload": {"tool": tool, "args": {}, "arg_refs": {}},
    }


def _trace(*events: dict) -> list:
    return parse_trace([json.dumps(e) for e in events])


# ── must_block: the pinned denial, not any denial ─────────────────────────────

class TestMustBlockHoldsThePinnedDenial:
    """`held` is a claim about the denial the trace recorded, not a headcount.

    The corpus row used to be computed from "does this config deny anything in
    this trace". A config that lets the exfil through and denies an unrelated
    benign call satisfies that, so the report said `held`, `safe_to_ship: true`,
    for a config that ships the breach.
    """

    events = _trace(_call(0, "exfil", "deny"), _call(1, "benign", "pass"))

    def test_the_recorded_denial_still_denying_is_held(self) -> None:
        row = regression_row("r", "must_block", "exfil", self.events,
                             kernel_config_from_json({"allowed_tools": ["benign"]}))
        assert row["result"] == "held"
        assert row["pinned_denials"] == 1
        assert row["escaped_denials"] == []

    def test_a_denial_elsewhere_does_not_cover_the_pinned_one(self) -> None:
        row = regression_row("r", "must_block", "exfil", self.events,
                             kernel_config_from_json({"allowed_tools": ["exfil"]}))
        assert row["result"] == "escaped"
        assert row["escaped_denials"] == [
            {"node_id": "n0", "seq": 0, "tool": "exfil", "gate": None}
        ]

    def test_every_pinned_denial_is_checked_not_just_the_first(self) -> None:
        two = _trace(_call(0, "exfil_a", "deny"), _call(1, "exfil_b", "deny"))
        row = regression_row("r", "must_block", "two exfils", two,
                             kernel_config_from_json({"allowed_tools": ["exfil_b"]}))
        assert row["result"] == "escaped"
        assert [d["tool"] for d in row["escaped_denials"]] == ["exfil_b"]
        assert row["pinned_denials"] == 2

    def test_a_denial_that_escapes_after_a_divergence_still_counts(self) -> None:
        """A config that denies something EARLIER makes the recorded tail
        counterfactual, so the escape below it might never be reached. It is
        still the finding: the gate that held this value no longer holds it,
        and excusing that because an unrelated earlier call was blocked is the
        same fail-open in a politer form."""
        events = _trace(_call(0, "benign", "pass"), _call(1, "exfil", "deny"))
        row = regression_row("r", "must_block", "exfil", events,
                             kernel_config_from_json({"allowed_tools": ["exfil"]}))
        assert row["first_divergence"] == 0  # benign is now capability-denied
        assert row["result"] == "escaped"
        assert [d["tool"] for d in row["escaped_denials"]] == ["exfil"]


class TestAPinWithNoRecordedDenialIsNotChecked:
    """A must_block trace recorded ungoverned has no boundary in it.

    Nothing in it marks the call that should have been blocked, so there is
    nothing to re-check. `unanchored` says so, and withholds `safe_to_ship` —
    the alternative is to guess which denial was meant and call the guess a
    verification.
    """

    def test_no_recorded_denial_is_unanchored(self) -> None:
        events = _trace(_call(0, "web_search", "pass"), _call(1, "post", "pass"))
        row = regression_row("r", "must_block", "escaped exfil", events,
                             kernel_config_from_json({"allowed_tools": ["post"]}))
        assert row["result"] == "unanchored"
        assert row["pinned_denials"] == 0

    async def test_an_unanchored_pin_withholds_safe_to_ship(
        self, client: httpx.AsyncClient,
    ) -> None:
        await client.post("/v1/ingest/run_obs", json={
            "node_id": "n0", "events": [_call(0, "web_search", "pass")],
        })
        await client.post("/v1/pins/run_obs",
                          json={"side": "must_block", "label": "observed"})
        report = (await client.post("/v1/regression", json={
            "config": {"allowed_tools": ["web_search"]},
        })).json()
        assert report["unanchored"] == 1
        assert report["safe_to_ship"] is False
        assert report["rows"][0]["result"] == "unanchored"


class TestMustPassRegressesOnANewDenial:
    def test_a_recorded_pass_that_now_denies_is_a_regression(self) -> None:
        events = _trace(_call(0, "notes_write", "pass"))
        row = regression_row("r", "must_pass", "legit", events,
                             kernel_config_from_json({"allowed_tools": []}))
        assert row["result"] == "regressed"
        assert row["new_denial"]["category"] == "capability"

    def test_a_recorded_denial_that_now_passes_is_not_a_regression(self) -> None:
        """must_pass asks "does the legitimate flow still pass".

        Relaxing a denial the flow already hit is a divergence, and a denial
        that was recorded and stays recorded is a denial left in the trace —
        the old check ("a divergence, and something denies") read the two
        together as a regression. Nothing that passed stopped passing.
        """
        events = _trace(_call(0, "relaxed", "deny"), _call(1, "still_denied", "deny"))
        row = regression_row("r", "must_pass", "legit", events,
                             kernel_config_from_json({"allowed_tools": ["relaxed"]}))
        assert row["first_divergence"] == 0        # relaxed: deny -> pass
        assert row["new_denial"] is None           # nothing that passed now denies
        assert row["result"] == "passed"


# ── the config parser: 400, never a coerced 200 ───────────────────────────────

class TestAMalformedConfigIsRefusedNotCoerced:
    """Every consumer of a kernel config answers 200 with a confident verdict
    computed from whatever survived parsing, so a config that parses into
    something other than what was written is worse than one that fails."""

    def test_a_bare_string_is_not_a_list_of_names(self) -> None:
        """``frozenset("slack_post")`` is nine single characters, so the tool
        the operator plainly meant to allow is not in its own capability table
        — and the request still answered 200."""
        with pytest.raises(ConfigInvalid) as exc:
            kernel_config_from_json({"allowed_tools": "slack_post"})
        assert "allowed_tools must be a list" in str(exc.value)

    def test_an_unknown_consequence_class_names_the_valid_ones(self) -> None:
        with pytest.raises(ConfigInvalid) as exc:
            kernel_config_from_json({"consequence_overrides": {"t": "NOPE"}})
        assert "CATASTROPHIC" in str(exc.value)

    def test_a_consequence_class_is_case_sensitive_and_says_so(self) -> None:
        with pytest.raises(ConfigInvalid):
            kernel_config_from_json({"consequence_overrides": {"t": "benign"}})

    def test_a_predicate_without_an_arg_is_refused(self) -> None:
        with pytest.raises(ConfigInvalid) as exc:
            kernel_config_from_json(
                {"value_policies": {"t": [{"kind": "enum", "allowed": ["a"]}]}})
        assert "'arg' is required" in str(exc.value)

    def test_a_cap_that_is_not_a_number_is_refused_here(self) -> None:
        """The kernel compares the cap with ``>=`` deep in the fold; a string
        there is a TypeError, i.e. a 500 for a typo in a text box."""
        with pytest.raises(ConfigInvalid):
            kernel_config_from_json({"budget_cap_calls": "ten"})

    def test_tool_weights_must_be_an_object_of_numbers(self) -> None:
        with pytest.raises(ConfigInvalid):
            kernel_config_from_json({"tool_weights": "heavy"})
        with pytest.raises(ConfigInvalid):
            kernel_config_from_json({"tool_weights": {"t": "heavy"}})

    async def test_the_route_answers_400_with_the_field(
        self, client: httpx.AsyncClient,
    ) -> None:
        await client.post("/v1/ingest/run_a", json={
            "node_id": "n0", "events": [_call(0, "t", "pass")],
        })
        for path, body in (
            ("/v1/replay/run_a", {"config": {"allowed_tools": "t"}}),
            ("/v1/regression", {"config": {"allowed_tools": "t"}}),
        ):
            resp = await client.post(path, json=body)
            assert resp.status_code == 400, path
            assert "allowed_tools" in resp.json()["error"]


class TestTheTwoConfigShapesCarryTheSameKeys:
    """The Config Builder shape and the direct kernel shape are the same
    config; the ``sinks`` branch used to drop keys the other one read."""

    def test_the_builder_shape_keeps_positional_sinks_and_overrides(self) -> None:
        cfg = kernel_config_from_json({
            "sinks": {"shell": {"consequence_class": "EXEC"}},
            "positional_sinks": ["shell"],
            "consequence_overrides": {"shell": "CATASTROPHIC"},
        })
        assert cfg.positional_sinks == frozenset({"shell"})
        assert cfg.consequence_overrides["shell"].name == "CATASTROPHIC"

    def test_a_misspelled_sink_class_is_refused(self) -> None:
        """It used to make the sink neither egress nor imperative: a config
        that reads as governed, gates nothing, and reports safe to ship."""
        with pytest.raises(ConfigInvalid) as exc:
            kernel_config_from_json({"sinks": {"s": {"consequence_class": "export"}}})
        assert "READ|WRITE|EXPORT|EXEC" in str(exc.value)
