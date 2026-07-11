"""The backend's JCS canonicalization is the kernel's, byte for byte.

These vectors are the SAME file the adapter side reads; asserting the backend
matches them is asserting adapter ⟷ backend agree on the signed bytes (protocol
§6). A drift here means a command the operator signs would verify on one side
and not the other.
"""
from __future__ import annotations

import json
import pathlib

import pytest
from axor_backend.errors import CommandRejected
from axor_backend.signing import jcs_canonical, signed_payload

VECTORS = json.loads(
    (pathlib.Path(__file__).parent / "vectors" / "jcs.json").read_text("utf-8")
)


@pytest.mark.parametrize("vec", VECTORS["ok"], ids=[v["name"] for v in VECTORS["ok"]])
def test_canonical_matches_vector(vec: dict) -> None:
    assert jcs_canonical(vec["input"]) == vec["canonical"].encode("utf-8")


@pytest.mark.parametrize(
    "vec", VECTORS["error"], ids=[v["name"] for v in VECTORS["error"]]
)
def test_float_vectors_rejected(vec: dict) -> None:
    with pytest.raises(CommandRejected):
        jcs_canonical(vec["input"])


def test_signed_payload_uses_canonical_bytes() -> None:
    """signed_payload wraps body in the fixed envelope and canonicalizes it —
    the vector pins the exact key order (body, node_id, timestamp, version)."""
    vec = next(v for v in VECTORS["ok"] if v["name"] == "signed_payload_shape")
    src = vec["input"]
    assert signed_payload(
        src["node_id"], src["version"], src["body"], src["timestamp"]
    ) == vec["canonical"].encode("utf-8")
