"""The keyring: a typo names the operator, and bad input is never a 500.

Two failures with the same root — this is the signature check, and both halves
of it reported the caller's problem (or the operator's) as the backend's.

Building the keyring happens inside `create_app`, so a bad key is a backend
that does not start. It used to not start with a raw
`ValueError: non-hexadecimal number found in fromhex() arg at position 0` — no
variable, no operator, a character offset for a diagnosis. And `.env.example`'s
third hardening step is to paste
`AXOR_OPERATOR_KEYS={"op_you":"<ed25519-pubkey-hex>"}`, so following it and
forgetting to substitute produced exactly that.

`verify` caught ValueError but not TypeError, and everything it receives came
off the wire as JSON — so `"sig": 123` or `"operator": []` escaped an
AUTHENTICATION check as `500 {"error": "internal"}`.
"""
from __future__ import annotations

import pathlib

import httpx
import pytest
from axor_backend.app import create_app
from axor_backend.env import EnvError
from axor_backend.errors import CommandRejected
from axor_backend.signing import OperatorKeyring

GOOD = "3b6a27bcceb6a42d62a3a8d02a6f0d73653215771de243a63ac048a18b59da29"


class TestABadKeyNamesTheOperator:
    @pytest.mark.parametrize(("what", "key", "says"), [
        (".env.example's own placeholder", "<ed25519-pubkey-hex>", "not hex"),
        ("one nibble short", "aa" * 31 + "a", "not hex"),
        ("a 16-byte key", "aa" * 16, "16-byte"),
        ("a 64-byte key (a private seed pasted whole)", "aa" * 64, "64-byte"),
        ("not a string at all", 12345, "int where"),
    ])
    def test_it_says_which_key_and_why(
        self, what: str, key: object, says: str,
    ) -> None:
        with pytest.raises(EnvError) as caught:
            OperatorKeyring({"op_alice": key})  # type: ignore[dict-item]
        message = str(caught.value)
        assert "AXOR_OPERATOR_KEYS" in message, what
        assert "op_alice" in message, what
        assert says in message, what

    def test_the_offender_is_named_among_good_keys(self) -> None:
        with pytest.raises(EnvError, match="op_bad"):
            OperatorKeyring({"op_ok": GOOD, "op_bad": "nonsense"})

    def test_a_good_keyring_loads(self) -> None:
        ring = OperatorKeyring({"op_ok": GOOD})
        assert ring.empty is False
        assert OperatorKeyring({}).empty is True

    def test_the_backend_refuses_to_start_on_one(
        self, tmp_path: pathlib.Path,
    ) -> None:
        """Where it actually bites: `create_app` builds the keyring, so this is
        a process that does not come up — which is right, and has to say why."""
        with pytest.raises(EnvError, match="op_you"):
            create_app(
                database_url=f"sqlite+aiosqlite:///{tmp_path}/a.db",
                operator_keys={"op_you": "<ed25519-pubkey-hex>"},
            )


class TestMalformedInputIsNeverOurFault:
    @pytest.mark.parametrize(("what", "operator", "sig"), [
        ("a sig that is a number", "op_ok", 123),
        ("a sig that is null", "op_ok", None),
        ("a sig that is a list", "op_ok", ["aa"]),
        ("an operator that is a list", ["op_ok"], "aa" * 64),
        ("an operator that is null", None, "aa" * 64),
    ])
    def test_it_is_command_rejected_not_a_type_error(
        self, what: str, operator: object, sig: object,
    ) -> None:
        ring = OperatorKeyring({"op_ok": GOOD})
        with pytest.raises(CommandRejected):
            ring.verify(operator, b"message", sig)  # type: ignore[arg-type]

    def test_a_hex_string_that_is_simply_wrong_still_says_sig_invalid(
        self,
    ) -> None:
        """The distinction that has to survive: malformed input is described,
        a real bad signature is not — `sig_invalid`, exactly, and nothing more.
        Whatever the crypto library says about WHY a signature failed is a
        detail of the check, and the caller who supplied it learns nothing from
        it that helps them except how to iterate."""
        ring = OperatorKeyring({"op_ok": GOOD})
        with pytest.raises(CommandRejected) as caught:
            ring.verify("op_ok", b"message", "aa" * 64)
        assert str(caught.value) == "sig_invalid"

    def test_a_non_hex_signature_is_the_same_answer(self) -> None:
        """Hex that will not decode is still just a wrong signature, and saying
        so differently would separate "malformed" from "wrong" for an attacker
        who controls both."""
        ring = OperatorKeyring({"op_ok": GOOD})
        with pytest.raises(CommandRejected) as caught:
            ring.verify("op_ok", b"message", "zz" * 64)
        assert str(caught.value) == "sig_invalid"

    @pytest.mark.parametrize(("what", "body"), [
        ("a sig that is a number", {"sig": 123}),
        ("a sig that is null", {"sig": None}),
        ("an operator that is a list", {"sig": "aa" * 64, "operator": ["x"]}),
    ])
    async def test_over_http_it_is_a_403_not_a_500(
        self, tmp_path: pathlib.Path, what: str, body: dict,
    ) -> None:
        app = create_app(
            database_url=f"sqlite+aiosqlite:///{tmp_path}/{abs(hash(what))}.db",
            operator_keys={"op_ok": GOOD}, allow_unsigned=False,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t",
        ) as c, app.router.lifespan_context(app):
            payload = {"state": {"paused": True}, "version": 1,
                       "operator": "op_ok", "timestamp": "t"} | body
            r = await c.post("/v1/plane/n1/command", json=payload)
            assert r.status_code == 403, f"{what}: {r.text}"
