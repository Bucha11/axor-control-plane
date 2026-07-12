# Axor Control Plane — Spec v2 (draft), Chapter 6: Federation Vault

Extends ui-spec §14.2 (single-node vault) to a federation. The load-bearing decision: **a federation has two kinds of secret, and they live under opposite rules.** Merging them into one store subordinates the stricter secret to the looser one and collapses the property the whole security model rests on.

| | Tool credentials | Operator signing keys |
|---|---|---|
| What | API keys for the agents' tools | ed25519 keys that sign commands & define the boundary |
| Purpose | injected at the sink so the agent never sees them | prove command authenticity end-to-end (protocol §6) |
| Vault operation | **dispense** — hand the secret to the proxy at call time | **sign** — accept a payload, return a signature, never the key |
| Centralize? | yes — federation-scoped, shared, rotatable | custody yes, disclosure never |
| If vault compromised | tool creds exposed (bad, bounded) | must remain UNABLE to forge federation commands |

These are two subsystems that happen to share the word "vault." This chapter specifies both and the wall between them.

---

## 1. Federation tool-credential vault (extends §14.2)

The §14.2 vault, scoped to a federation instead of one node.

- **Federation-scoped custody.** One vault holds tool credentials for all nodes in the federation; a node fetches by (tool, endpoint) at call time, injected at the sink (§14.2 unchanged). Nodes no longer each carry their own creds — the convenience the single-node vault couldn't offer.
- **Scope is still per (tool, endpoint), enforced per node.** Federation-wide storage does not mean federation-wide access: a node gets a credential only for a (tool, endpoint) its own config declares. A compromised `web-scraper` cannot pull the `payments` credential just because both live in the same federation vault — enrollment scope is per node, checked at dispense. Shared storage, partitioned access.
- **Injection is still proxy-side, sink-bound, byte-for-byte** (§14.2): the credential exists where the effect happens and nowhere upstream; a prompt-injected agent redirecting a call gets no credential (scope mismatch).
- **Rotation is federation-wide config** (versioned), not a control-plane command — the plane may *revoke* federation-wide in an incident (narrowing), never grant (§12.0 rule extended to the federation).
- **Break-glass: fail closed, federation-wide** (§14.2 decision #14 unchanged): vault down → injection impossible → typed denial at every node. No cached creds anywhere, ever.
- **Inter-federation:** a foreign peer's credentials are never in our vault. We hold, at most, our own credential for authenticating *to* the peer — a tool credential like any other, scoped to that peer endpoint. Their creds are theirs (ch1 boundary).

## 2. Operator signing-key custody — vault SIGNS, never surrenders

The federation's ed25519 operator keys (protocol §6) may be *custodied* in a vault, but the operation is inverted and the private key never leaves.

- **Delegated signing, not key dispensing.** For tool creds the vault hands out a secret; for signing keys the vault **takes a payload and returns a signature**. The private key lives in an HSM/KMS-class backend and is never emitted — not to a node, not to the plane, not to the operator's browser. Different API, different verb: `dispense(tool, endpoint) → secret` vs `sign(key_id, payload) → signature`.
- **This preserves the property §6 depends on.** §6's guarantee is that a compromised backend can withhold or delay commands but cannot *forge* them, because signing requires the operator's private key which the backend never holds. Custody-with-delegated-signing keeps this exactly: the vault holds the key, but a caller must be authorized to *request a signature*, and even a compromised vault caller can only get signatures over payloads it submits — it cannot exfiltrate the key to sign offline at leisure or to impersonate the federation elsewhere.
- **Pubkeys are not secrets.** Verification keys stay in local adapter config (protocol §6, unchanged) — never fetched from the vault, so a compromised vault cannot swap the verification keys a node checks against. Custody covers the *private* half only; the public half's whole security value is that it lives where verification happens, pinned.
- **Authorization to sign is itself a scoped, audited capability.** Which operators may request signatures for which federation key is config (multi-operator keyset, parked on team features — protocol §4). Every `sign` request is logged: who, which key, payload hash. A signature is an operator action and belongs in the audit trail beside the command it authorizes.

## 3. The wall between the two

- **Separate backends, separate credentials to reach them.** The tool-cred vault and the signing custody are not one service with two modes; compromising the ability to dispense tool creds must not grant the ability to request signatures. A single "vault admin" role spanning both would recreate the single-point-of-forgery this whole chapter avoids.
- **Different failure blast radius, by design.** Tool-cred vault compromised → tool creds exposed, agents can't be trusted to hold effects, incident is bad but bounded to data access. Signing custody compromised → attacker can request signatures but cannot extract keys; with per-request authorization and audit, forged commands are detectable and scoped, not silent and total. The wall ensures one breach is not both.
- **Peer pubkeys (ch1) touch neither vault** — public, config-resident, no custody needed.

## 4. UI (ties §14.2, federation topology)

- Federation settings show two panes, visibly separate: **Tool credentials** (enrolled tools, scope per node, rotation, health) and **Signing keys** (custody backend, operators authorized to sign, sign-request audit log). The separation is not just backend — the UI must not let an operator conflate "manage our API keys" with "manage who can command the federation."
- A `sign` request appears in the same trace/audit surface as the command it produced — an operator action, first-class (§12.3).

## 5. Decisions & open

- **Two secrets, two subsystems, one wall** is the chapter. Tool creds: federation-scoped, dispensed, fail-closed. Signing keys: custodied, signed-not-surrendered, pubkeys pinned in config. Never one store.
- **Backward compat:** a single-node federation with no signing custody is exactly §14.2 — operator keys in local config, tool vault optional per node. The federation vault is additive; nothing about the single-node path changes.
- Open [verify-by-design]: signing-custody latency on the command path — `sign` is a network round-trip to the custody backend per operator command. Commands are low-frequency (operator-issued, not per-agent-step), so latency is tolerable; confirm it stays off the *enforcement* path entirely (enforcement is local and unsigned by operators — §12.0; only operator commands are signed, and those are already interactive-latency-tolerant).
- Open: HSM vs software-KMS for signing custody at the self-hosted tier — self-hosted orgs may not have an HSM; lean: pluggable custody backend (HSM / cloud-KMS / software-keystore), same `sign` interface, security posture declared per deployment — mirrors the vault backend decision (monetization / arch: build on existing stores, don't roll crypto).
