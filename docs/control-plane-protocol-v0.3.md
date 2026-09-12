# Axor Control Plane — Protocol Note (v0.3)

v0.3: **the snapshot is signed too** (section 3). v0.2 said in section 3 that state is LWW so "reconnect is trivially correct", and in section 6 that a compromised backend "cannot forge a pause, stop, injection or attestation". Both were implemented, and the first undid the second: a delta was verified against operator keys and the snapshot carrying the same fields was not, so a compromised plane could forge anything by sending it as a snapshot instead. The snapshot now carries `commands` — the signed command behind each key of `state` — and the adapter verifies every field before applying any of them.

v0.2: adds `pending_excision` (spec 8.2.1, v0.13) mirroring the injection one-shot pattern (section 4a); resolves two of the section 9 open items (canonicalization = JCS RFC 8785; heartbeat static T=10s, stale=3T).

Implements spec §12.0 (advisory overlay, local enforcement) and decision #7 (SSE + POST, declarative desired state). This note fixes the wire semantics before implementation. Threat model recap: the control plane is a privileged *advisory* channel; a compromised plane must not be able to compromise a governed agent.

---

## 1. Connection topology

The adapter dials **out** only. Two flows, both initiated by the adapter:

```
adapter ──SSE  GET /v1/plane/{node_id}/desired ──▶ backend   (downstream: state + facts)
adapter ──POST /v1/plane/{node_id}/telemetry  ──▶ backend   (upstream: events, batched)
operator UI ──POST /v1/plane/{node_id}/command ──▶ backend   (writes desired state / facts)
```

No listening socket on user infrastructure. The backend never connects *to* anything.

## 2. Two kinds of downstream payload — the core distinction

| Kind | Semantics | Merge rule | Examples |
|---|---|---|---|
| **Desired state** | target configuration of a node | lattice, versioned, last-write-wins | `paused`, `stopped`, `budget_cap`, `pending_injection`, `pending_excision` |
| **Facts** | append-only log entries the adapter adds to its local fact log | append-only, never replaced | `operator_attestation` (and its revocation) |

The distinction is load-bearing. Desired state answers "what should the node's posture be" — idempotent, snapshot-able, safe to replay. Facts feed the degradation recompute (`level = max(severity(uncovered facts))`, spec decision #9) — they must be append-only or the "no reset button" guarantee dies at the protocol layer. Attestation travels as a fact, not as state, and that is exactly why it is the one sanctioned descent path without violating "commands cannot widen": it does not mutate posture, it extends the fact log with a typed, signed, attributable entry for which the trust model defines semantics.

## 3. Desired state — schema and lattice

```json
{
  "node_id": "…",
  "version": 42,
  "state": {
    "stopped":  false,
    "paused":   true,
    "budget_cap": {"calls": 200, "cost": 14.0},
    "pending_injection": {
      "id": "inj_9f2c",
      "text": "…operator-authored…",
      "reason": "probing recovery behavior",
      "operator": "op_dmitrii",
      "sig": "ed25519:…"
    }
  }
}
```

- `version`: monotonic per node, assigned by the backend on each accepted command. Adapter applies iff `version > applied_version`, at the IntentLoop boundary only.
- **Lattice rules:** `stopped: true` is absorbing — once applied, later writes to `paused`/`pending_injection` are accepted into state but have no effect, and the adapter reports them as `noop_absorbed`. `budget_cap` is **decrease-only at the adapter**: a desired cap above the locally known cap is rejected locally (`rejected_widening`) regardless of what the backend sent — enforcement of the narrowing rule lives in the adapter, not in backend validation (a compromised backend must not be able to widen).
- **Snapshot semantics on (re)subscribe:** the SSE stream opens with one `snapshot` event carrying the full current desired state **and the signed commands behind it**, then pushes deltas. Because state is LWW, there is nothing to buffer or replay on the command direction — reconnect is trivially correct. (Telemetry direction handles durability separately, §5.)

```json
{
  "node_id": "…", "version": 42,
  "state": {"paused": true},
  "commands": [
    {"version": 41, "delta": {"paused": true},
     "operator": "op_dmitrii", "timestamp": "…", "sig": "ed25519:…"}
  ]
}
```

- **`commands`: one entry per key of `state`** — whichever command last wrote it. Kept per key rather than as a log, so it is bounded by the size of the lattice and not by how long the node has run. A single command that wrote several keys appears once.
- **The adapter verifies every entry exactly as it verifies a delta** (§6), then replays them in version order through the same apply path, so the lattice rules above hold identically whether a command arrives live or after a reconnect.
- **A key in `state` that no verified command accounts for refuses the WHOLE snapshot** (`sig_invalid`, reported upstream). Partial application would be the backend choosing which half of an operator's command takes effect. Keys the commands cover that `state` does *not* are the allowed direction: clearing a consumed one-shot removes a key and cannot forge anything, which is why a consumption ack needs no signature.
- `version` may be ahead of the last signed command for exactly that reason. The adapter takes it: the version is a counter, and a backend inflating it can only make the node ignore later commands — the withhold/delay §6 already allows, never a forged effect.
- **Unsigned deployments** (no operator pubkeys in adapter config — the open dev posture, which the backend logs loudly at boot) keep plain LWW snapshot semantics. There is nothing to verify against, and pretending otherwise would refuse every snapshot on a deployment that signs nothing.

## 4. Injection — at-most-once by id

One-shot semantics inside a declarative model: `pending_injection` is state (LWW — a newer unconsumed injection replaces an older one), consumption is a fact. The adapter applies an injection **at most once per `id`**: on the next context assembly it injects, emits `injection_consumed {id}` upstream, and remembers the id. Replays of the same state (snapshot after reconnect) are no-ops against the consumed-id set. The backend clears `pending_injection` on receiving the consumption event. If the node is stopped or the connection is not test-bench-flagged (spec decision #5), the adapter emits `injection_refused {id, reason}` and never applies.

## 4a. Context excision (self-heal) — same one-shot pattern

Spec 8.2.1 (v0.13): self-heal removes the drifting context segment. On the wire it is
`pending_excision` in desired state (LWW; a newer unconsumed excision replaces an older
one), consumption is a fact:

```json
"pending_excision": {
  "id": "exc_41d0",
  "target_refs": ["v_8a12", "v_90ff"],
  "reason": "refusal drift after prompt update, re-anchoring",
  "operator": "op_dmitrii",
  "sig": "ed25519:…"
}
```

- **At-most-once per `id`**, same consumed-id set as injections. On apply the adapter emits
  a `context_excision` kernel event carrying the causal_root refs (and hashes, per
  persistence rules) of removed values — replay folds the removal deterministically.
- **Provenance guard is adapter-side, like all enforcement.** If any target segment carries
  operator-config provenance, the adapter refuses the whole excision and emits
  `excision_refused {id, reason: "operator_config_provenance", refs: [...]}` — deleting a
  restriction is widening via deletion, and a compromised backend must not be able to
  request it into effect. Partial application is not allowed (no silently-narrower heal).
- Downstream heat/attestation semantics are unchanged: excision removes content, it never
  vouches (spec 8.2.1); Sentinel branches end at the excision event.
- After consumption the backend clears `pending_excision` and the plane service schedules
  the verifying re-probe (spec 8.2.1 "heal -> verify, one gesture").

## 5. Telemetry upstream

- Same JSONL kernel events as the trace — no second schema. Batched POST with a local disk-backed queue and retry; `Idempotency-Key` per batch; backend dedupes.
- Heartbeat every T seconds carrying `applied_version` + `consumed_injection_ids` tail + degradation level + budget remaining. The heartbeat is how the UI knows a command actually landed (the topology view renders *reported* state next to *desired* state; divergence between them is rendered, not hidden).
- Trace remains the system of record; telemetry loss degrades UI liveness, never governance.

## 6. Signatures — surviving a compromised backend

Channel security (TLS + per-connection token) authenticates *the plane*. It does not protect against the plane itself being compromised. Therefore, per spec §12.0, commands with agent-side effect are **signed end-to-end by the operator**:

- Operator keypair: Ed25519. Public keys are placed in the **adapter's local config** by the operator — never delivered over the channel (else a compromised backend swaps keys).
- Signed payload: `(node_id, version, canonical_state_delta | fact, timestamp)`. The adapter verifies before applying; unsigned or badly signed commands are dropped and reported (`sig_invalid`). **This covers the snapshot as well as the delta** — see §3 — because a channel with one verified path and one unverified path has no verified path.
- Consequence: a fully compromised backend can withhold or delay commands (liveness) but cannot forge a pause, stop, injection, or attestation (integrity). Advisory overlay, enforced cryptographically.
- Facts (attestations) additionally embed the signature into the fact log entry itself — the Sentinel graph stores who signed, and revocation requires a signature from the same org's keyset.

## 6a. Peer channel hooks (forward declaration, inter-federation)

Not implemented in v0.2; shapes reserved so nothing here has to move when
inter-federation A2A ships (spec v2 Ch.1):

- **Channel establishment verifies the peer declaration from local config** —
  identity (pubkey), trust level, allowed message classes. Undeclared peer = L0
  (fail-closed, same posture as an undeclared sink).
- **`governance_attested`** additionally verifies the peer's signed attestation
  of kernel version + config hash at channel establishment, and re-verifies on
  any config-hash change. The attestation pins accountability (fact of
  governance), never semantics — foreign configs are not parsed into local
  trust decisions (spec v2 decision v2-6).
- **L2 assertion envelope is native-protocol-only** (spec v2 decision v2-4):
  signed label assertions ride the peer channel's own envelope. MCP-as-A2A
  channels are pinned L0/L1 at establishment — retrofitting assertions into MCP
  metadata would fragment verification.
- Peer pubkeys live in local config beside operator pubkeys (§6) and are never
  delivered over any channel or fetched from any vault.

## 7. Disconnect behavior

- Default: fail-continue under local config (spec §12.0). Adapter-local option `hold_on_disconnect: {after: 120s}` → node sets itself `paused` locally; this is adapter config, not backend state — the backend cannot cause or prevent it.
- Backend marks a node `stale` in the topology view after missed heartbeats; stale is a UI state, never a governance state.

## 8. Failure modes

| Failure | Effect | Because |
|---|---|---|
| Channel down | agent continues (or self-pauses if opted in); commands queue as LWW state, delivered as snapshot on reconnect — **with the signatures they arrived with**, so reconnect is not a way around §6 | §3 snapshot semantics |
| Backend compromised | can delay/withhold; cannot forge commands, cannot widen budget, cannot swap operator keys | §6 e2e signatures, §3 adapter-side narrowing check |
| Operator key leaked | attacker can pause/stop/inject on test-bench connections until key revoked in adapter config | key rotation = adapter config change; flagged as the residual risk |
| Duplicate/replayed delta | no-op | version monotonicity + consumed-id set |
| `pause` after `stop` | absorbed, reported `noop_absorbed` | lattice |

## 9. Open

- ~~Canonicalization for signed payloads~~ **resolved v0.2: JCS (RFC 8785)**; canonicalization vectors ship with the plane service (`packages/axor-backend/tests/vectors/`), and the cross-side payload → canonical-bytes → ed25519-signature triples live at repo root as **`test-vectors/jcs-signing.json`** (spec v2 Ch.6 deliverable) — both the adapter and the plane service verify them in CI.
- ~~Heartbeat period T and stale threshold~~ **resolved v0.2: static, T=10s, stale=3T.** Adaptive tuning only on evidence.
- Multi-operator orgs: keyset format in adapter config (list of pubkeys + roles) — align with team-features policy hook (spec decision #8) when it lands.
