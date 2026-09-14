"""Vault mode is armed only against a backend this deployment shares fate with.

§14.2 decision #14 is `fail closed`: vault unreachable → no credential → the
tool call is denied, and no TTL cache is permitted because it "would reintroduce
the secret-on-proxy this feature exists to remove". That is the right call, and
it makes the backend's uptime part of whether the agent's tools work at all.

The spec answers the resulting question for exactly one shape — "availability is
solved where it belongs — vault HA, co-location on self-hosted" — and
`docs/spec-v2-multiagent.md` records that the other shape has none: "fail-closed
across someone else's uptime has no answer here yet, where self-hosted answers
it with co-location".

Envelope mode already closed CUSTODY for a remote backend: with a sealing key
registered the plane holds what it cannot open. This is UPTIME, which envelope
mode does not touch. So co-location stops being an assumption and becomes a
check, and the one way past it is an operator saying so.

The proxy's own deployment is unaffected: compose points the proxy at
`http://backend:8400` on the bridge network and gates its start on the backend
being healthy, so the name resolves to a private address before this runs.
"""
from __future__ import annotations

import logging
import pathlib

import pytest
from axor_proxy.app import ProxyState
from axor_proxy.vault import (
    ALLOW_REMOTE_ENV,
    CredentialVault,
    VaultPostureRefused,
    check_vault_colocation,
)

VAULTED = frozenset({"web_search"})
TOOLS = {"web_search": "http://upstream.test/search"}


def state(tmp_path: pathlib.Path, backend: str | None, **over: object) -> ProxyState:
    return ProxyState(tools=TOOLS, trace_dir=tmp_path, backend_url=backend,
                      vault_tool_names=VAULTED, **over)  # type: ignore[arg-type]


class TestCoLocatedBackendsArm:
    @pytest.mark.parametrize("backend", [
        "http://127.0.0.1:8400",      # the same machine
        "http://[::1]:8400",          # ...and its v6 spelling
        "http://10.1.2.3:8400",       # a compose/k8s/VPC network
        "http://192.168.4.5:8400",
        "http://172.17.0.2:8400",     # the docker bridge, which is what compose gives
    ])
    def test_it_arms(self, tmp_path: pathlib.Path, backend: str) -> None:
        assert state(tmp_path, backend).vault is not None


class TestSomebodyElsesUptimeIsRefused:
    @pytest.mark.parametrize(("backend", "why"), [
        ("http://1.1.1.1:8400", "public address"),
        ("http://vault.nowhere.invalid:8400", "does not resolve"),
        ("http://169.254.169.254:8400", "link-local"),
    ])
    def test_it_refuses(
        self, tmp_path: pathlib.Path, backend: str, why: str,
    ) -> None:
        with pytest.raises(VaultPostureRefused) as exc:
            state(tmp_path, backend)
        assert why in str(exc.value)

    def test_the_refusal_names_the_decision_and_the_way_through(
        self, tmp_path: pathlib.Path,
    ) -> None:
        """A refusal an operator cannot act on is a wall, not a check."""
        with pytest.raises(VaultPostureRefused) as exc:
            state(tmp_path, "http://1.1.1.1:8400")
        message = str(exc.value)
        assert "fails closed by design" in message
        assert "§14.2" in message
        assert ALLOW_REMOTE_ENV in message

    @pytest.mark.parametrize("backend", ["not-a-url", "http://", "http:///v1"])
    def test_a_url_with_no_host_is_refused_as_one(
        self, tmp_path: pathlib.Path, backend: str,
    ) -> None:
        """The MESSAGE is the assertion, not the refusal.

        Dropping the host check still refuses — `getaddrinfo(None)` raises, so
        it comes back out as "does not resolve" — which made an earlier version
        of this test pass with the check removed. A backend URL with no host in
        it is a configuration mistake, and saying "'' does not resolve" sends
        the operator to look at DNS.
        """
        with pytest.raises(VaultPostureRefused) as exc:
            state(tmp_path, backend)
        assert "needs a backend URL with a host" in str(exc.value)


class TestTheOverrideIsExplicitAndLoud:
    def test_it_arms_and_says_what_was_accepted(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv(ALLOW_REMOTE_ENV, "1")
        with caplog.at_level(logging.WARNING, logger="axor.proxy"):
            assert state(tmp_path, "http://1.1.1.1:8400").vault is not None
        assert len(caplog.records) == 1
        message = caplog.records[0].message
        assert "DENIED" in message
        assert "not this deployment's to control" in message

    def test_any_other_value_does_not_count(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """"true", "yes" and "0" are not the opt-in; only "1" is, the same
        spelling every other posture flag in this deployment uses."""
        for value in ("true", "yes", "0", ""):
            monkeypatch.setenv(ALLOW_REMOTE_ENV, value)
            with pytest.raises(VaultPostureRefused):
                state(tmp_path, "http://1.1.1.1:8400")


class TestItOnlyAppliesWhereItMeansSomething:
    def test_passthrough_is_untouched(self, tmp_path: pathlib.Path) -> None:
        """§6 stays literally true for a proxy with no tool opted in: no vault
        mode, no cost, nothing to check."""
        plain = ProxyState(tools=TOOLS, trace_dir=tmp_path,
                           backend_url="http://1.1.1.1:8400",
                           vault_tool_names=frozenset())
        assert plain.vault is None

    def test_a_proxy_with_no_backend_is_untouched(
        self, tmp_path: pathlib.Path,
    ) -> None:
        assert state(tmp_path, None).vault is None

    def test_an_injected_vault_is_an_embedding_seam(
        self, tmp_path: pathlib.Path,
    ) -> None:
        """A caller passing its own client owns the transport, so the URL beside
        it is not a deployment fact. Nothing in the product takes this branch —
        `main.cli` always builds its own — and this test is here so that stays a
        decision rather than a hole nobody noticed.
        """
        injected = CredentialVault("http://1.1.1.1:8400")
        assert state(tmp_path, "http://1.1.1.1:8400",
                     vault=injected).vault is injected


class TestTheCheckItself:
    def test_it_is_callable_without_a_proxy(self) -> None:
        check_vault_colocation("http://127.0.0.1:8400")
        with pytest.raises(VaultPostureRefused):
            check_vault_colocation("http://1.1.1.1:8400")

    def test_the_argument_beats_the_environment(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(ALLOW_REMOTE_ENV, "1")
        with pytest.raises(VaultPostureRefused):
            check_vault_colocation("http://1.1.1.1:8400", allow_remote=False)
