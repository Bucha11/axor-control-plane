"""One reader for the environment, and what it refuses.

Three files had grown their own parsers and all three failed the same way — a
value the parser did not understand became a default, or an exception that
quoted the value without naming the variable.
"""
from __future__ import annotations

import pytest
from axor_backend import env


class TestAFlagUnderstandsTheUsualSpellings:
    """`_flag` accepted exactly "1", so `AXOR_WEBHOOK_BLOCK_PRIVATE=true` was
    False — and that one fails OPEN: it is the switch that refuses webhooks
    aimed at loopback and RFC1918 space."""

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "Yes", "on", " on "])
    def test_true(self, value: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AXOR_TEST_FLAG", value)
        assert env.flag("AXOR_TEST_FLAG") is True

    @pytest.mark.parametrize("value", ["0", "false", "NO", "off", ""])
    def test_false(self, value: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AXOR_TEST_FLAG", value)
        assert env.flag("AXOR_TEST_FLAG") is False

    def test_unset_takes_the_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AXOR_TEST_FLAG", raising=False)
        assert env.flag("AXOR_TEST_FLAG") is False
        assert env.flag("AXOR_TEST_FLAG", True) is True

    def test_something_it_does_not_understand_is_refused_not_assumed(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Loudly, because quietly assuming False is the bug."""
        monkeypatch.setenv("AXOR_TEST_FLAG", "enabled")
        with pytest.raises(env.EnvError, match="AXOR_TEST_FLAG"):
            env.flag("AXOR_TEST_FLAG")


class TestANumberNamesItsVariable:
    def test_a_typo(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AXOR_TEST_NUM", "thirty")
        with pytest.raises(env.EnvError, match="AXOR_TEST_NUM='thirty' is not a number"):
            env.number("AXOR_TEST_NUM")

    def test_a_floor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AXOR_TEST_NUM", "0")
        with pytest.raises(env.EnvError, match="must be >= 1"):
            env.number("AXOR_TEST_NUM", minimum=1)

    def test_unset_and_empty_both_mean_the_default(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("AXOR_TEST_NUM", raising=False)
        assert env.number("AXOR_TEST_NUM", 5.0) == 5.0
        monkeypatch.setenv("AXOR_TEST_NUM", "   ")
        assert env.number("AXOR_TEST_NUM", 5.0) == 5.0

    def test_a_deliberate_zero_survives(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`_number(...) or DEFAULT` ate it: AXOR_SCHEDULE_SWEEP_SECONDS=0
        silently became 60. The reader can tell absent from set, so the default
        belongs to it."""
        monkeypatch.setenv("AXOR_TEST_NUM", "0")
        assert env.number("AXOR_TEST_NUM", 60.0) == 0.0

    def test_an_integer_refuses_a_float(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AXOR_TEST_NUM", "1.5")
        with pytest.raises(env.EnvError, match="whole number"):
            env.integer("AXOR_TEST_NUM", 1)


class TestAJsonObjectNamesItsVariable:
    def test_malformed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AXOR_OPERATOR_KEYS", "op_a=abc")
        with pytest.raises(env.EnvError, match="AXOR_OPERATOR_KEYS is not valid JSON"):
            env.json_object("AXOR_OPERATOR_KEYS")

    def test_not_an_object(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AXOR_OPERATOR_KEYS", '["op_a"]')
        with pytest.raises(env.EnvError, match="must be a JSON object, got list"):
            env.json_object("AXOR_OPERATOR_KEYS")


class TestTheSettingsThatUsedToFailSilently:
    def test_the_ssrf_guard_turns_on_for_the_spelling_people_use(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from axor_backend.config import AppConfig

        for name in ("AXOR_IDENTITY_JWKS", "AXOR_IDENTITY_JWKS_URL"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("AXOR_WEBHOOK_BLOCK_PRIVATE", "true")
        assert AppConfig.resolve().webhook_block_private is True

    def test_a_zero_sweep_interval_reaches_the_config(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`_number(...) or DEFAULT` in `resolve` turned a deliberate 0 into 60,
        so the reader keeping zero is only half the fix."""
        from axor_backend.config import AppConfig

        monkeypatch.setenv("AXOR_SCHEDULE_SWEEP_SECONDS", "0.5")
        assert AppConfig.resolve().schedule_sweep_seconds == 0.5
        monkeypatch.setenv("AXOR_SCHEDULE_SWEEP_SECONDS", "0")
        with pytest.raises(env.EnvError, match="AXOR_SCHEDULE_SWEEP_SECONDS"):
            AppConfig.resolve()

    def test_the_identity_issuer_has_an_environment_variable(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """It had none while every setting beside it did, so a deployment
        running its own identity could not be configured to log anyone in."""
        from axor_backend.config import AppConfig

        monkeypatch.setenv("AXOR_IDENTITY_ISSUER", "https://id.acme.example")
        assert AppConfig.resolve().identity_issuer == "https://id.acme.example"
        # An explicit argument still wins over the environment.
        assert AppConfig.resolve(identity_issuer="axor-identity").identity_issuer == (
            "axor-identity"
        )
