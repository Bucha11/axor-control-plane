# Axor Control Plane — Spec v2 (draft), Chapter 2: Protocol Delta (protocol → v0.2)

Amends control-plane-protocol-v0.1. Three changes and a decisions record. Everything not mentioned stands.

---

## 1. Canonicalization: JCS (RFC 8785) — decided

Signed payloads (`(node_id, version, canonical_state_delta | fact, timestamp)`, protocol §6) are canonicalized with **JCS**. Rationale: a published RFC an auditor recognizes beats a house convention; conforming libraries exist for Python and TS. Deliverable attached to this decision: `test-vectors/jcs-signing.json` — a fixed set of payload → canonical-bytes → signature triples that both the adapter and the plane service verify in CI. A signing implementation that can't reproduce the vectors doesn't ship.

## 2. Third one-shot command: `context_excision`

Self-heal (ui-spec §8.2.1, v0.13) needs a wire form. It joins injection in the one-shot family — the desired-state document gains:

```json
"pending_excision": {
  "id": "exc_4b09",
  "segment_refs": ["cr_8a12", "cr_77e0"],
  "reason": "refusal drift after prompt update, re-anchoring",
  "operator": "op_dmitrii",
  "sig": "ed25519:…"
}
```

Semantics mirror injection exactly (protocol §4): LWW while pending, **at-most-once by id**, consumption reported upstream as `excision_consumed {id}`, replays no-op against the consumed-id set. Adapter-side guards before applying:
- **Provenance guard** (ui-spec §8.2.1 v0.13): every `segment_refs` entry must resolve to a non-operator-config segment; any operator-config ref → `excision_refused {id, reason: "operator_config_provenance"}`, nothing applied — the command is atomic, no partial excision.
- Test-bench flag NOT required (unlike injection): excision is subtractive of non-config content and reason-gated; it is however still an intervention — mid-run it marks the run `intervened`.
- Trace event `context_excision` carries the refs and hashes per persistence rules; taint on already-derived values is untouched.

The one-shot family is now: `pending_injection` (additive, test-bench-gated), `pending_excision` (subtractive, provenance-guarded). Future one-shots follow this template: LWW-pending + consumed-fact + at-most-once by id + adapter-side guard.

## 3. Peer channel hooks (forward declaration, inter-federation)

Not implemented in v0.2; shapes reserved so nothing here has to move when inter-A2A ships (spec v2 ch1):
- Channel establishment verifies peer declaration (pubkey, level) from local config; `governance_attested` additionally verifies the signed kernel-version + config-hash attestation, re-verified on config-hash change.
- L2 assertion envelope is native-protocol-only (ch1 decision #2); MCP-as-A2A channels are pinned L0/L1 at establishment.

## 4. Decisions recorded in passing

| Item | Decision |
|---|---|
| Multi-operator keyset format | **Blocked on team features** — intentionally parked, not open. Single-operator keyset (list of one) ships; format extension lands with the attestation policy hook (spec decision #8). |
| Heartbeat T=10s, stale=3T | **Verify-by-experiment**: criterion — first live adapter run; stale false-positive rate < 1/day per node on a healthy link, else raise T. |
| Demo composition | **Single-graph demo confirmed; split-screen governed/ungoverned demoted to the landing's second screen.** ui-spec §7 wording ("split-screen is the demo hero") to be amended at v2 merge. |
