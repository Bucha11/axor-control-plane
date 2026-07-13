"""The spec-v2 Ch.6 signing gate: payload → canonical bytes → ed25519 signature.

`test-vectors/jcs-signing.json` (repo root) is the shared deliverable both the
adapter and the plane service verify in CI. If the backend cannot reproduce the
canonical bytes or verify the signatures, a command an operator signs on one
side would not verify on the other — that implementation doesn't ship.
"""
from __future__ import annotations

import json
import pathlib

import pytest
from axor_backend.errors import CommandRejected
from axor_backend.signing import OperatorKeyring, signed_payload
from nacl.signing import SigningKey

VECTORS_PATH = (
    pathlib.Path(__file__).resolve().parents[3] / "test-vectors" / "jcs-signing.json"
)
DOC = json.loads(VECTORS_PATH.read_text("utf-8"))
IDS = [v["name"] for v in DOC["vectors"]]


@pytest.mark.parametrize("vec", DOC["vectors"], ids=IDS)
def test_canonical_bytes_reproduce(vec: dict) -> None:
    p = vec["payload"]
    assert signed_payload(
        p["node_id"], p["version"], p["body"], p["timestamp"]
    ) == vec["canonical"].encode("utf-8")


@pytest.mark.parametrize("vec", DOC["vectors"], ids=IDS)
def test_signature_verifies_via_keyring(vec: dict) -> None:
    """The backend's own verify path (OperatorKeyring) accepts each triple —
    the exact code path plane commands go through, not a parallel one."""
    ring = OperatorKeyring({"op_test": DOC["public_key_hex"]})
    p = vec["payload"]
    message = signed_payload(p["node_id"], p["version"], p["body"], p["timestamp"])
    ring.verify("op_test", message, vec["signature_hex"])  # raises on mismatch


@pytest.mark.parametrize("vec", DOC["vectors"], ids=IDS)
def test_signature_reproduces_from_seed(vec: dict) -> None:
    """The triples are re-derivable from the committed test seed — the vector
    file cannot silently drift from its generator."""
    key = SigningKey(bytes.fromhex(DOC["seed_hex"]))
    sig = key.sign(vec["canonical"].encode("utf-8")).signature
    assert sig.hex() == vec["signature_hex"]


def test_tampered_payload_fails() -> None:
    ring = OperatorKeyring({"op_test": DOC["public_key_hex"]})
    vec = DOC["vectors"][0]
    p = vec["payload"]
    tampered = signed_payload(p["node_id"], p["version"] + 1, p["body"], p["timestamp"])
    with pytest.raises(CommandRejected):
        ring.verify("op_test", tampered, vec["signature_hex"])
