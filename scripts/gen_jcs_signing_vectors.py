"""Generate test-vectors/jcs-signing.json — the spec-v2 Ch.6 §1 deliverable.

A fixed set of payload → canonical-bytes → ed25519-signature triples that both
the adapter (axor-core) and the plane service verify in CI. A signing
implementation that can't reproduce these vectors doesn't ship.

The key below is a TEST KEY, derived from a public constant. It must never be
used outside these vectors; its whole point is that both sides of the wire can
re-derive the same signatures deterministically.

Run: uv run python scripts/gen_jcs_signing_vectors.py
"""
from __future__ import annotations

import hashlib
import json
import pathlib

from axor_backend.signing import signed_payload
from nacl.signing import SigningKey

# Deterministic test seed — sha256 of a public sentence, so the file is
# reproducible from source alone.
SEED = hashlib.sha256(b"axor-control-plane jcs-signing test vectors v1").digest()

# One vector per signed-payload family the protocol actually uses: the desired
# state deltas (protocol §3), both one-shots (§4/§4a), and a fact (§5).
CASES: list[tuple[str, str, int, dict, str]] = [
    # (name, node_id, version, body, timestamp)
    ("pause", "node-a", 7, {"paused": True}, "2026-07-12T00:00:00Z"),
    ("stop_absorbing", "node-a", 8, {"stopped": True}, "2026-07-12T00:00:01Z"),
    ("budget_cap", "node-b", 3,
     {"budget_cap": {"calls": 200, "cost": 14}}, "2026-07-12T00:00:02Z"),
    ("pending_injection", "node-c", 12,
     {"pending_injection": {"id": "inj_9f2c", "text": "operator-authored",
                            "reason": "probing recovery", "operator": "op_test"}},
     "2026-07-12T00:00:03Z"),
    ("pending_excision", "node-c", 13,
     {"pending_excision": {"id": "exc_4b09", "segment_refs": ["cr_8a12", "cr_77e0"],
                           "reason": "refusal drift, re-anchoring",
                           "operator": "op_test"}},
     "2026-07-12T00:00:04Z"),
    # Facts are signed with version=0 (signing.py convention).
    ("attestation_fact", "node-d", 0,
     {"fact_id": "f_301", "kind": "operator_attestation",
      "causal_root": "cr_11aa", "reason": "reviewed, benign", "operator": "op_test"},
     "2026-07-12T00:00:05Z"),
    # Key-order and unicode stress inside a signed body.
    ("unicode_and_order", "node-é", 1,
     {"z": [3, 2, 1], "a": {"β": "χ", "A": "Z"}, "n": None}, "2026-07-12T00:00:06Z"),
]


def main() -> None:
    key = SigningKey(SEED)
    vectors = []
    for name, node_id, version, body, ts in CASES:
        canonical = signed_payload(node_id, version, body, ts)
        sig = key.sign(canonical).signature
        vectors.append({
            "name": name,
            "payload": {"node_id": node_id, "version": version,
                        "body": body, "timestamp": ts},
            "canonical": canonical.decode("utf-8"),
            "signature_hex": sig.hex(),
        })
    out = {
        "_comment": (
            "spec-v2 Ch.6 deliverable: payload -> JCS canonical bytes -> ed25519 "
            "signature triples. TEST KEY ONLY (seed is public by design). Both the "
            "adapter and the plane service must reproduce these in CI. Regenerate "
            "with scripts/gen_jcs_signing_vectors.py"
        ),
        "algorithm": "ed25519",
        "seed_sha256_of": "axor-control-plane jcs-signing test vectors v1",
        "seed_hex": SEED.hex(),
        "public_key_hex": key.verify_key.encode().hex(),
        "envelope": "jcs({body, node_id, timestamp, version})",
        "vectors": vectors,
    }
    root = pathlib.Path(__file__).resolve().parents[1]
    path = root / "test-vectors" / "jcs-signing.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", "utf-8")
    print(f"wrote {path} ({len(vectors)} vectors)")


if __name__ == "__main__":
    main()
