# Axor Control Plane — UI & Connectivity Specification (high-level, v0.14)

v0.14: title renamed "Axor Eval" -> "Axor Control Plane" to match the platform name (bundle loose end 3). "Axor Eval" below refers to the Eval surface/product line inside the platform, and to the core measurement spec, unchanged.

Scope: how a user connects their agent and experiences the product. Companion to the Axor Eval core spec (which defines what is measured). This document is the *delivery surface* — UI, connection modes, onboarding funnel.

Design constraint inherited from core: **the EvidenceCase is the primary artifact.** Every screen exists to produce, display, or aggregate reproducible discrepancy cases. The score is never the hero; the caught discrepancy is.

Second design constraint (v0.8): **quiet until wrong.** A screen answers one user question with one hero element; system state is disclosed on demand, not by default. Healthy is rendered as near-silence (a dot and a name); density is earned exclusively by problems. Level, heat, budget, desired-vs-reported and similar instrumentation appear only when abnormal, diverged, or explicitly expanded. Every tab has one primary action visible; the rest live behind "more". A dense expert view may exist later as an explicit opt-in mode — never as the default. Rationale: v1 mock rendered *system state* and read like a cockpit; the product must render *answers* (what was caught / who needs attention / what happened at step N).

v0.2 additions: Config Builder (§11), Control tab (§12), Sentinel taint graph + Probe health check panels on the Eval surface (§8), and the resulting observe/intervene separation rule (§12.3).

v0.3 additions: Control tab promoted to a real-time **control plane** with an explicit advisory-overlay architecture (§12.0) — enforcement stays local, the plane never enters the decision path. Probe health check simplified to one-shot verdict (§8.2). Sentinel heat **reset replaced by per-branch attestation** — append-only, per causal_root, mapped to an explicit downgrade fact in the degradation machine (§8.1.1, §12.2).

v0.13: self-heal mechanism fixed — **context excision** (§8.2.1): heal removes the drifting context segment. Provenance guard: excision may never remove operator-config-provenance segments (deleting a restriction is widening via deletion). Excision is a traced event carrying refs of removed values; replay folds it deterministically.

v0.12: **Self-heal trigger** on the health check panel (§8.2.1) — axor-probe's already-wired self-heal gets an explicit UI trigger. Explicit-only (never automatic on drift), executes as a plane command (reason required, signed, traced), then auto re-probes to verify.

v0.11: persona-gap pass. **Notifications** (§16) — the loud-elsewhere companion to quiet-until-wrong: webhook emission on level transitions / heat thresholds / discrepancy-bearing runs; emit-and-route, never a pager. **EvidenceCase export & share** (§8.3) — PDF + revocable permalink, observations only, never raw bodies. Regression report and onboarding screens mocked (no spec change — already §13.2/§5).

v0.10: Config Builder sinks gain **per-argument allowlists as a first-class editor** (declare arg → trusted value set → supersession) and **criticality** (critical/standard) with fixed semantics: criticality amplifies fact severity and evidence ranking, never relaxes gates on standard sinks.

v0.9: Config Builder gains **code-in, wrapped-out** mode (§11.3) — upload agent/tools code, sink candidates auto-detected from it, consequence classes human-confirmed, output is a wrapped package. Detection fills names, never classes: unclassified = denied.

v0.8: **quiet-until-wrong** added as the second product-wide design constraint (header), validated against the v2 UI mock.

v0.7: **all open questions resolved** — §10 converted to a decisions log; normative text patched throughout. Highlights: declarative control-plane commands (desired state, §12.0), degradation level as recompute over uncovered facts (§8.1.1), set-membership persistence for hosted replay (§13.4), vault break-glass = fail-closed typed denial (§14.2).

v0.6 additions: **Budget caps** (§15) — operator-set spend/call limits, enforced locally as config; exhaustion is a typed fact in the degradation machine; subtree budgets close delegation amplification; control plane may lower caps mid-run, raising goes through config ("commands cannot widen" extended). Budget-exhaustion added as an Eval fault scenario.

v0.5 additions: **Future scope** section (§14) — multi-agent & A2A support (federation surfaced end-to-end: A2A channel as governed sink/source, peer trust levels, cross-agent scenarios in Eval) and **Key Vault** (centralized credential custody with sink-side injection — keys leave the agent context entirely; explicitly reverses the §6 passthrough rule, trust implications spelled out).

v0.4 additions: **Replay mode** (§13) — time-travel scrubber over recorded traces plus **deterministic counterfactual governance replay**: edit tool availability / config, or inject synthetic taint at any step, and re-evaluate gates, taint flow, and Sentinel heating over the recorded actions without any LLM call. Sound up to first divergence (§13.2); live re-execution fork deferred (§13.3).

---

## 1. Two products in one, by intent

| Surface | Question it answers | User intent | Input required |
|---|---|---|---|
| **Demo** | "What does this thing even do?" | Curiosity | None — a button |
| **Get Started** | "How does *my* agent behave?" | Commitment | Connect an agent |

These are kept separate. Demo is a showcase, not an onboarding step. Get Started is reached only by users the demo already convinced.

v0.2 note: with the Control plane (§12) there is now a third intent — "operate/steer my governed agent" — but it is not a third onboarding surface. Control is unlocked *by* the adapter path, it never recruits users into it. The funnel (§3) is unchanged.

---

## 2. Connection model — variability by depth

Two orthogonal axes of choice. The user picks where they sit on each.

### Axis A — observability depth (what Eval can see)
| Mode | Sees | Unlocks | Effort |
|---|---|---|---|
| **Proxy** | Agent from outside: tool requests, tool responses, final claim | Core layer entirely — fabricated tool result, budget mismatch, canary, claim-vs-reality | ~5 min, no code change |
| **Adapter** | Agent from inside via axor-core: governance flow, taint, degradation, policy, federation | Full 4-property coverage — cross-session taint, cross-agent leak, policy laundering. **Plus: Control plane (real-time, v0.3), Sentinel taint graph + branch attestation (§8.1.1), Probe health check** | Wrap in `Invokable` — now assisted by Config Builder (§11) |

### Axis B — deployment (where the proxy runs)
| Mode | Traffic goes | For |
|---|---|---|
| **Self-hosted proxy** | localhost — nothing leaves | Prod, sensitive data, privacy-critical |
| **Hosted proxy** | through our infra (passthrough auth) | Non-prod, quick trials |
| **Demo mock-tools** | our synthetic broken tools | First contact, zero creds |

**Single contract across all modes:** `EvidenceCase` looks the same regardless of connection. Proxy fills a subset of fields; adapter fills the same subset plus governance fields. UI greys out unavailable panels with "available with adapter" — the missing data is the built-in, honest upsell. The v0.2 panels (taint graph, health check, Control) extend this same pattern: visible, greyed, labelled by what unlocks them.

---

## 3. The funnel

```
Demo (button · our agent · our faults · recorded)
   "agents really do this?"
        ↓
Get Started → connect own agent
        ↓
   Demo-mode (own agent · OUR mock broken tools · zero creds)
   "my agent fabricates too"
        ↓
   Proxy (own agent · OWN real tools)
     ├── hosted   (trial, non-prod)
     └── self-hosted (prod, private)
        ↓
   Adapter (full governance coverage)          ← Config Builder lives here (§11)
   "I want cross-session / policy / federation"
        ↓
   Control (operate the governed topology)     ← retention layer, not acquisition (§12)
```

Each step is sold by value already seen, not by promise. Barrier rises exactly as motivation to cross it rises. Nothing is pushed; depth opens on demand.

---

## 4. Demo (the showcase)

**Purpose:** in ~10 seconds, trigger the recognition moment. Not a feature tour — one caught lie, fully legible.

- No agent, no input, no config. One button.
- Runs **our** example agent against **our** broken tools.
- **Deterministic & pre-recorded** (via Replay, the same engine behind §13) — never a live LLM call. The showcase cannot depend on model stochasticity; first impression must be reproducible every time.
- Shows the single strongest EvidenceCase, e.g.:
  ```
  🔴 web_search → ToolError(timeout)
     agent: "Based on the search results, the answer is..."
  ```
- Goal of the screen: the user thinks "stop — agents really do that?" If the first screen doesn't produce that, the funnel never starts.

This is the lead asset: landing page, README gif, cold-outreach attachment.

---

## 5. Get Started (onboarding)

Reached on intent. Three steps, designed so the point of possible failure is moved *out* of the value moment into routine setup.

```
1. "What tools does your agent use?"
   • add tool: name + real endpoint URL
   • or: import MCP config → endpoints auto-parsed
   • or: pick demo-mode (our mock tools, skip this)

2. "Point your agent at the proxy"
   • UI shows ready base_url per tool
   • user copies into their agent config

3. "Connection check"  ← critical
   • test button: passthrough ping per endpoint
   • green/red per tool BEFORE any experiment
   • auth proves out here, calmly, not mid-run
```

**Why step 3 matters:** it relocates the only likely failure (auth/connectivity) from "middle of the value moment" to "routine pre-flight." A red light during setup is fine; a red screen mid-experiment ruins the impression. This is how the first run is protected.

**MCP is the smooth case:** endpoints already declared in MCP config → step 1 auto-fills. Near-zero friction, and the strongest demo case given MCP supply-chain is a live research topic.

**v0.2:** for users choosing the adapter path, step 1's tool inventory feeds directly into the Config Builder (§11) — the sink list is the same data, entered once.

---

## 6. Proxy behavior (the rules that make it "just work")

- **Auth is passthrough, untouched.** Agent sends its `Authorization` → proxy forwards byte-for-byte → returns response. Proxy never parses, substitutes, or stores creds. It intervenes at exactly two points: inject fault (if scenario requires) + record observation (for EvidenceCase). Everything else is clean passthrough. If the proxy doesn't touch auth, there's nothing for it to break.
- **No raw bodies persisted on hosted by default** (mirrors core `persist_inputs=False`). EvidenceCase stores the observation (claim vs reality, canary presence), not a dump containing keys.
- **Explicit visibility notice before hosted:** "the hosted proxy sees your tools' requests and responses during a run; for prod or sensitive data, run the proxy locally."
- **Endpoint lifetime (v0.7, decision #1):** hosted proxy URL is persistent per user but accepts traffic only while a run/session is armed; disarmed it returns 503. Configure-once stability without a permanently open endpoint that forwards arbitrary auth headers.
- **Observe-only by default:** proxy injects faults and records, but does not block the agent (blocking changes behavior, contaminating the measurement — the core observe-only requirement made concrete at the transport layer). v0.2/v0.3: the Control plane deliberately breaks observe-only; how that is fenced off is defined in §12.3, and the rule here remains unchanged for the Eval surface.

---

## 7. Governed vs Ungoverned comparison

Same agent, same fault — run once without axor-core, once under it. Closes the ecosystem loop: Eval catches the problem, Core prevents it, shown side-by-side.

### Two things this can mean — keep them distinct
- **(A) Enforcement on/off** — clean and demo-ready. Ungoverned agent exfiltrates / executes the injection; governed agent receives a `denial` (taint→export blocked, bash-after-external-read denied). Verifiable: the action either happened or was blocked.
- **(B) Governance changes behavior** — subtler. Under governance the agent sees different context (compressed, taint-limited) and structured denials, and may fabricate more or less as a result. Real but must be measured carefully — not all of it is "enforcement caught it." Start with (A); treat (B) as a later research observation.

### Reuses Scenario Delta — no new metric
Same Delta mechanic, comparison axis is *governance presence* instead of *fault presence*:
```
Ungoverned + Permission Revocation:  integrity = 0.41   (fabricates success)
Governed   + Permission Revocation:  integrity = 0.94   (denial, not fabrication)
Governance Delta:                    +53%
```
EvidenceCase in governed mode carries an extra `intent_denied` event at the point where the ungoverned agent did the opposite — a two-trace side-by-side on one timeline. Strongest single visualization in the product: cause and effect in one frame.

### Context split — demo-hero vs research-optional (important)
This feature shifts Eval's stance from *neutral measurement* toward *marketing for Core*. Handle by context:
- **Demo / product surface:** make it the hero. Split-screen — bare agent lies (left), under axor-core caught (right). Sells the whole ecosystem in 10 seconds.
- **Academic artifact** (the reproducible run for outreach): keep Eval **neutral**. Measure multiple models *without* axor-core; show governance comparison, if at all, as a separate honest "effect of enforcement" experiment with a disclaimer — never as the headline result. A researcher who sees your eval always concluding your own Core wins will rightly suspect conflict of interest.

One feature, two stances: hero in demo, optional side-experiment (caveated) in research.

---

## 8. Experiment screen (post-connection) — the Eval tab

High-level only; detail later.

- **Configure:** pick scenario or suite, deprivation mode(s), trigger timing. Combined faults supported (chaos surface). Optional: governed/ungoverned toggle (§7).
- **Run:** live or recorded.
- **Live audit view:** stream of events, colour-coded (🔴 fabricated/unrecorded · 🟠 policy/memory · 🟡 omission/substitution · 🟢 consistent).
- **EvidenceCase view:** the receipt — observed reality | agent claim | trace | influence ranking. Replayable — opens directly into Replay mode (§13) at the relevant step.
- **Results:** Scenario Delta vs baseline (headline) · Core scores · Experimental scores (CI, fenced) · suite breakdown (Core/Experimental separate). *MVP cut (v0.7, decision #4): configure/run + live stream + full EvidenceCase view ship first; influence ranking and CI-fenced experimental scores land in v1.1. EvidenceCase view is never cut.*
- **Greyed panels** for adapter-only data, labelled — the visible upsell.

### 8.1 Taint graph panel (axor-sentinel) — new in v0.2

Visualizes the per-value taint/provenance graph for the run (and, with Sentinel connected, across sessions).

- **Per-run view (adapter, no Sentinel required):** causal_root graph of the current run — nodes are values/reads/agents, edges are derivation; each node shows integrity taint + confidentiality floor. This is the visual form of the per-value taint model the adapter already records; it renders straight from EvidenceCase governance fields.
- **Cross-session view (Sentinel required):** the Neo4j-backed reputation/taint graph — where a tainted value came from *sessions ago*, which agents/tools it passed through, which exports it reached or was denied at. Filter by causal_root, by tool, by session. **Default cut (v0.7, decision #6):** k-hop neighborhood of the current focus (the causal_root or node navigated from), expand-on-click with server-side pagination; "hottest branches" available as a second lens.
- **EvidenceCase linkage:** clicking a graph edge opens the EvidenceCase event that created it. The graph is a navigation surface over cases, not a separate artifact — the primary-artifact constraint holds.
- **Availability:** greyed on proxy-only ("available with adapter"); cross-session sub-view additionally greyed without Sentinel ("connect axor-sentinel").
- **Read-only.** Taint graph on the Eval tab observes; the one taint-affecting action — branch attestation — lives on the Control plane (§12.2) and is *defined* here in §8.1.1 because its semantics are Sentinel-graph semantics.

### 8.1.1 Branch attestation (v0.3) — heat reset done right

Operators need a way to bring a "hot" node back into service after investigating. A reset implemented as *deletion or zeroing* would be an operator-side reputation-laundering channel: accumulated cross-session evidence erased by one button. So reset does not exist. **Attestation** does:

- **Attestation is an append-only fact, not a mutation.** The operator selects a **causal_root branch** in the taint graph and attests it. An `operator_attestation` event is written into the Sentinel graph: who, when, reason (free text, required), scope (the causal_root), prior heat. Nothing is deleted; reputation is *recomputed downward over* the attestation. Revoking an attestation is itself a new event — full history, both directions.
- **Scope is per-branch, node-wide as shortcut.** Primary UX: click the suspicious branch in the graph (§8.1) → attest exactly that lineage. "Attest node" exists as a convenience and expands, visibly, to attestations of every currently-hot branch on that node — the operator sees the list of what they are vouching for before confirming. No blind amnesty: node-wide attestation is sugar over per-branch events, never a separate coarser primitive.
- **Degradation stays monotone over facts.** Attestation is not a rewind but a **new fact of a new type**. Semantics fixed in v0.7 (decision #9): **the level is a pure recompute, `level = max(severity(uncovered facts))`**, where a fact is covered iff an unrevoked attestation spans it. No transition table for descent, no "partial descent" ambiguity — coverage changes, the function re-evaluates. Monotonicity over the fact sequence is preserved (facts only accumulate; attestations are facts too); the level itself may descend as coverage grows. Closes the "monotone, but there's a reset button" objection with one line of math instead of a rulebook.
- **Attestation does not endorse values.** It lowers *reputation/heat* (Sentinel axis); it does not lift per-value integrity taint or confidentiality floor on data still in flight. Endorsement of values remains the kernel's bounded-codomain mechanism only. An attested branch whose values re-trigger gate denials heats right back up — attestation is "I checked, resume watching," not "trust this forever."
- **Authority (v0.7, decision #8):** single operator + required reason field. An org-level policy hook (N confirmations for high-heat branches) ships with team features, as configuration — not hardcoded.
- **Visibility.** Topology view (§12.1) shows attested nodes as "reset by X at T, prior heat H"; the taint graph renders attestation events as first-class nodes on the affected branch.

### 8.2 Health check panel (axor-probe) — new in v0.2, simplified in v0.3

One-shot behavioral health check: run → verdict. Deliberately not a monitoring product.

- **Mechanic:** shadow-comparison probes from axor-probe run once against the connected agent; result is a per-family green/orange/red plus one overall verdict. No continuous shadowing, no scheduling, no alerting in MVP — continuous drift monitoring is a separate later product mode, not a v1 panel.
- **Placement:** two entry points — (a) inside Get Started as an optional "step 4: baseline health check" (extends the §5 pre-flight philosophy: known failures surface *before* the value moment), and (b) a panel on the Eval tab showing the last verdict; a drift-over-time sparkline appears only once ≥2 manual checks exist.
- **Relation to Eval scores:** health check is *not* an Eval metric and must not be blended into Scenario Delta or Core scores. Drift answers "has my agent changed?"; Eval answers "does my agent lie under fault?". Separate panels, separate vocabulary. A drift-red agent with a green integrity score is a legitimate, informative combination.
- **Availability:** probe needs to invoke the agent directly — works on proxy mode (probes go through the same proxied endpoints) *and* adapter mode; the adapter additionally attributes drift to governance state (e.g. degradation level at probe time).

### 8.2.1 Self-heal trigger (v0.12)

axor-probe's self-heal routine is already wired at the runtime level; this gives it an explicit product trigger.

- **Explicit-only, forever.** Self-heal never fires automatically on a drift verdict. Automatic behavior-correction triggered by a behavior-drift signal is a closed loop that modifies the agent with no human in it — exactly the class of loop a governance product exists to prevent. The verdict recommends; the operator triggers.
- **Rendered on the health panel, owned by the plane.** The button appears where the operator sees the drift (a red/orange probe family), but the action is a plane-service command in every respect that matters (§12.3): reason field required, operator-signed (protocol §6), delivered over the command channel, recorded in the trace as an intervention event. Placement is UX; semantics are Control's. Adapter connections only; on proxy connections the button is greyed with the standard label.
- **Heal → verify, one gesture.** Triggering self-heal automatically schedules a re-probe of the affected families on completion. The panel shows the pair as one unit: "refusal drift · healed by op_dmitrii 12:41 → re-probe: OK". A heal without a verifying re-probe is not rendered as resolved.
- **Mid-run rule:** if triggered while an experiment is running, the run is marked `intervened` (§12.3) — a healed agent mid-measurement is a different agent.
- **Failure honesty:** if re-probe still shows drift, the panel says so ("healed → re-probe: still drifting") and the heal event remains in the trace either way. No optimistic green.

**Mechanism (v0.13): context excision.** Self-heal removes the context segment the probe identified as driving the drift. Consequences, fixed:

- **Provenance guard — the load-bearing rule.** Deletion is not inherently narrowing: removing accumulated conversational residue is safe; removing a *restrictive instruction* widens behavior through deletion. Therefore excision may only target segments whose provenance is NOT operator config. If the probe attributes drift to an operator-config segment, heal refuses with that diagnosis — that is a config problem to fix in config, not a segment to delete. Same soundness shape as everywhere else: operator config is inaccessible to runtime-triggered modification.
- **Traced as `context_excision`** carrying the causal_root refs (and hashes, per persistence rules) of removed values — replay needs it: context assembly after the heal differs, and the fold accounts for the removal deterministically. The affected Sentinel branches end in the excision event; heat is untouched — excision removes content, it does not vouch for anything (that's attestation's job, and conflating them would reopen the laundering channel).
- **Taint is not lifted.** Values already *derived from* the excised segment keep their taint; excision removes future influence, not past provenance.

### 8.3 EvidenceCase export & share (v0.11)

The primary artifact must be able to leave the product, or it isn't primary.

- **Export:** single EvidenceCase → PDF (the receipt: observed reality | claim | verdict | trace excerpt | replay reference) for reports, incident reviews, papers.
- **Share:** revocable permalink with a scoped read token — one case, no navigation to the rest of the workspace. Serves the "engineer shows the boss" and "red-teamer files the finding" moments without account provisioning.
- **Content rule:** export carries observations, labels, and verdicts — never raw request/response bodies. Consistent with §6 persistence: if it wasn't stored, it can't leak via export; if it was stored under set-membership persistence (decision #10), the export shows membership, not values.

---

## 9. What's deliberately deferred

- Team features, SSO, saved-experiment history → later (monetization layer).
- Side-by-side multi-model comparison → after single-agent flow is solid.
- ~~Adapter constructor wizard (guided codegen for hand-rolled agents) → after proxy path proves demand.~~ **Superseded in v0.2:** the sink-driven part of the constructor ships now as the Config Builder (§11). What stays deferred is the *full* guided codegen for arbitrary hand-rolled agent loops; the Config Builder deliberately covers only "declare sinks → get config + wrapper scaffold", which is the 80% case.
- Control-tab write-actions beyond pause/stop/inject/replan (e.g. live policy editing, gate toggling) → later; each new intervention type widens the observe/intervene boundary (§12.3) and must be justified individually.
- Larger roadmap items (multi-agent/A2A end-to-end, Key Vault) → §14, kept separate because each changes the trust story rather than just adding surface.

---

## 10. Decisions log (all resolved in v0.7)

Former open questions, now fixed. Format: decision → rationale in one line. Normative text in the referenced sections is patched accordingly; this log is the record.

| # | Question | Decision |
|---|---|---|
| 1 | Hosted proxy endpoint lifetime | **Persistent per-user URL, active only while a run/session is armed; 503 otherwise.** Stability of persistent + attack window of ephemeral. (§6) |
| 2 | Demo-mode mock tool archetypes | **Two at launch: web_search + generic MCP tool.** Search = recognition moment; MCP = supply-chain relevance + reuses §5 MCP import. Retrieval duplicates search mechanically; auth archetype contradicts zero-creds demo-mode. |
| 3 | Framework adapters order | **LangChain/LangGraph polish (axor-langchain exists) → CrewAI when §14.1 multi-agent lands → generic Config Builder wrapper covers the tail.** No second adapter until proxy-path demand names a framework. (§11) |
| 4 | Experiment screen MVP depth | **Configure/run + live colour-coded audit stream + full EvidenceCase view.** Stream is cheap (SSE over the existing event feed). Influence ranking and CI-fenced experimental scores → v1.1. EvidenceCase view is never cut — primary artifact. (§8) |
| 5 | Injection into production agents | **Allowed only on connections flagged test-bench; every injection requires a reason field (symmetric with attestation) and marks the run `intervened`.** (§12.2, §12.3) |
| 6 | Sentinel graph default cut | **k-hop neighborhood of current focus (causal_root / node navigated from); "hottest branches" as second lens; expand-on-click with server-side pagination.** Fixed last-N-sessions rejected: arbitrary N, can cut an active branch. (§8.1) |
| 7 | Control plane transport & ordering | **SSE (telemetry) + POST (commands); commands are declarative desired-state per node, versioned, last-write-wins.** Ordering hazards (pause-after-stop) dissolve: there is no command sequence, only target state. Reuses solved SSE infrastructure (auth header, buffering, sticky sessions). (§12.0) |
| 8 | Attestation authority | **Single operator + required reason now; org-level policy hook (require N confirmations) arrives with team features.** Hardcoded two-operator kills the feature for solo/small teams. (§8.1.1) |
| 9 | Degradation downgrade mapping | **Level is a pure recompute: `level = max(severity(uncovered facts))`, where covered = attested.** "Partial descent" question dissolves; full-coverage rule is the special case. Fixed in the trust model. (§8.1.1) |
| 10 | Replay fidelity vs persist_inputs=False | **Set-membership persistence: hosted stores the index/hash of the matched element of the operator-declared set + all labels/verdicts, never values; replay resolves against local config.** Enum-predicate counterfactuals need membership, not content — full-counterfactual tier on hosted with zero values persisted. Fidelity tiers remain only for undeclared args. (§13.4) |
| 11 | Replay corpus membership | **Auto-pin traces containing an EvidenceCase (must-block side) + manual pin of legitimate traces (must-pass side); hosted retention = quota on pinned count.** A regression corpus needs both sides or "block everything" passes CI. (§13.2) |
| 12 | A2A protocol order | **Native Invokable tree first (already covered) → MCP-as-A2A adapter (agent exposed as MCP server, reuses §5 import) → Google A2A on demonstrated demand.** Boundary semantics fixed in §14.1 precisely so adapters stay thin. |
| 13 | Vault backend | **Existing secret store (Vault/KMS-class) + our (tool, endpoint) scoping and trace integration; lightweight encrypted-file dev backend for low-friction start.** Own crypto storage is a red flag in a security product; differentiator is scoped sink-side injection, not storage. (§14.2) |
| 14 | Vault break-glass | **Hard fail: vault down → injection impossible → typed denial, fail-closed.** TTL credential cache reintroduces the secret-on-proxy the feature exists to remove. §12.0 disconnect-safety says *enforcement* must not degrade offline — and deny IS enforcement working. Availability is a vault-HA problem. Bonus: honest-reporting-under-credential-loss becomes an Eval scenario. (§14.2) |
| 15 | Budget cost model & time windows | **v1 = operator-declared per-tool weights (deterministic → replay-compatible); provider billing = reporting overlay only, never an enforcement input; time-window budgets deferred** (availability feature, not governance; if built, windows run on trace time to preserve replay determinism). (§15) |
---

## 11. Config Builder (new in v0.2) — "describe sinks → get config + wrapped agent"

**Purpose:** collapse the adapter barrier. The user describes *what their agent's sinks are*; the builder emits (a) a complete axor-core config and (b) a wrapped-agent scaffold. No reading of kernel docs required for the common case.

### 11.1 Flow

```
1. Declare sinks
   • per tool/endpoint: name, sink_type (consequence class),
     direction (read/write/export/exec), trust level of origin
   • criticality (v0.10): critical | standard (default standard)
   • auto-seeded from Get Started step 1 / MCP import where possible —
     the tool inventory is entered once, reused here
   • UI offers the closed sink_type taxonomy as a picker, not free text —
     "unknown sink is denied" is surfaced as an explicit line in the preview,
     so fail-closed is a visible choice the user confirmed, not a surprise

1b. Declare budgets (optional, v0.6)
   • per node / per subtree / per run: max tool calls, max cost
     (per-tool weights), optional time window
   • undeclared budget = unlimited — budgets are opt-in limits,
     not fail-closed defaults (unlike sinks); the preview says so explicitly

2. Declare arguments (optional, per sink)
   • first-class allowlist editor (v0.10): pick an argument by name,
     declare its trusted value set (enum) or bounded numeric range →
     enables supersession for exactly that argument
   • anything not declared stays under full taint — safe default

3. Preview
   • generated config side-by-side with a plain-language reading:
     "export sinks require untainted values or a satisfied enum
      predicate over your declared set; bash after external read → denied"
   • gate sequence shown as the fixed pipeline with the user's
     values slotted in — the config is parameters, never new gates

4. Emit
   • config file (versioned, diffable)
   • wrapper scaffold: agent wrapped in `Invokable` / `GovernedNode`,
     framework-specific where known (axor-langchain first), generic otherwise
   • "run first governed experiment" CTA → lands on Eval tab with the
     governed/ungoverned toggle (§7) pre-armed — immediate payoff
```

### 11.2 Constraints

- **Criticality semantics (v0.10) — amplification, never permission.** `critical` raises the severity of facts generated at that sink (plugging directly into `level = max(severity(uncovered facts))`, decision #9 — denials at critical sinks escalate degradation faster) and ranks its EvidenceCases first in results. `standard` is the default and means *default* strictness: marking other sinks critical relaxes nothing anywhere. Importance is a one-way amplifier; if it could lower guarantees on standard sinks it would be a fail-open channel dressed as UX.
- **The builder parameterizes the kernel; it never generates policy logic.** Output space is: sink declarations, trust sets, enum/range predicates, capability table entries. Gate sequence, degradation machine, taint propagation are not user-assembled — that is the whole point of the kernel/trust-model split, and the UI must not blur it.
- **Everything undeclared is untrusted.** The builder's defaults are the fail-closed defaults; the UI's job is to make relaxations explicit and visible, never to pre-relax for convenience.
- **Config is the artifact, builder is disposable.** Emitted config must be a plain reviewable file a user can hand-edit and version; the builder is a convenience front-end over it, not a required runtime component. Re-import of a hand-edited config back into the builder for further editing is in scope.
- **Relation to §9:** this ships the sink-declaration slice of the deferred adapter constructor. Full agent-loop codegen stays deferred.

### 11.3 Code-in, wrapped-out (v0.9)

Third entry point beside MCP import and manual declaration: **upload the agent and its tools, receive everything wrapped.**

```
1. Drop code (agent folder / tools.py / MCP manifest)
2. Analysis extracts sink candidates: tool decorators, function
   signatures, framework registrations (LangChain tools first),
   MCP manifests — names, endpoints, argument shapes
3. Candidates land in the builder UNCLASSIFIED — the user assigns
   each a consequence class; nothing proceeds until all are classified
4. Emit: wrapped package — original code untouched + generated
   wrapper module + axor.config.json + run instructions
```

Hard rules:
- **Detection fills names and signatures, never consequence classes.** Inferring "this looks like an export" and being wrong is a silent relaxation of fail-closed. Classification is the human step by design, not a temporary limitation. An unclassified candidate is treated as undeclared: denied.
- **The user's code is never modified.** The wrapper is a separate module importing their entry point; the emitted package diff against the upload is exactly two new files. Reviewable, revertible.
- **The §9 boundary stands:** we wrap tools and the entry point; we do not rewrite arbitrary agent loops. If the entry point can't be identified, the builder says so and falls back to the scaffold + instructions, not to guessing.
- **Where analysis runs:** hosted upload gets the same explicit visibility notice as the hosted proxy (§6). The private path is **self-hosting** — the wrap engine ships in the deployment image (`uv sync --all-packages` installs it), so on a self-hosted stack `POST /v1/wrap/scan` runs on the operator's own machine and the source never leaves their infrastructure. Self-hosted parity is a launch requirement, not a follow-up, and it is met by the deployment rather than by a second code path.

  This previously specified a `axor wrap ./my_agent` CLI that would open the builder pre-filled. That command never existed, and building it would have been a bridge to nowhere: it solves a strict subset of what self-hosting already solves, for the one user who is evaluating the hosted product and will end up self-hosting anyway. The CLI that *does* exist — `axor-wrap scan | manifest | config` — is the repo-and-CI surface for generating tool manifests that get committed and fed to the governor. That is its job; the builder's file upload is the convenience version of it, not the other way round.

---

## 12. Control plane (v0.2 as tab; v0.3 real-time) — operate the governed topology

**Purpose:** once an agent (or a tree of agents) runs under the adapter, the same governance instrumentation that lets Eval *see* also gives an operator surface to *steer* — live. Control is that surface. Adapter-only by construction — the proxy has no handle on internal topology.

### 12.0 Architecture — advisory overlay, local enforcement (v0.3, load-bearing)

Real-time control means a bidirectional channel: telemetry out of the IntentLoop (cheap, safe) and a command channel into the agent (pause / stop / replan / inject). The command channel is a new privileged entry point into every connected agent — the single biggest risk the feature introduces. A compromised control plane must not mean compromised agents. Hence the fixed rule:

> **Enforcement is local and in-process; the control plane is an advisory overlay. It never becomes part of the decision path.**

Consequences, non-negotiable:

1. **Disconnect-safe.** Channel loss → the agent continues under its local config, unchanged. Commands are best-effort. An opt-in per-connection policy `hold_on_disconnect` exists for operators who prefer fail-stop over fail-continue — but the *default* is that governance guarantees never depend on network availability. If gates ever wait on the plane, latency and availability kill both the product and the K-level guarantees.
2. **Commands cannot widen.** Same rule as operator injection (§12.2): commands carry operator provenance, not trusted-config status. A command can pause, stop, force replan, inject one turn of context, or attest a taint branch (§8.1.1) — it can never grant capabilities, lift taint directly, or relax a gate. Persistent relaxation goes through config: versioned, reviewed, redeployed.
3. **Authenticated, signed, self-hostable.** Commands are signed per-operator; the channel is mutually authenticated. For prod the control plane is self-hosted, mirroring the self-hosted proxy option — same trust story on both surfaces (§2, Axis B).
4. **Telemetry is the same event stream Eval records.** No second instrumentation path: the plane subscribes to the IntentLoop event feed the adapter already emits. One trace, two consumers (Eval scores it, Control renders it live).

Mechanics carried over from v0.2 unchanged: pause takes effect at the IntentLoop boundary only — never mid-effect — the flag now simply arrives over the channel instead of from a local UI.

**Protocol (fixed in v0.7, decision #7):** telemetry over SSE, commands over POST. Commands are **declarative** — each command sets the desired state of a node (`{paused: true}`, `{budget_cap: N}`, `{stopped: true}`), versioned, last-write-wins. There is no imperative command sequence to order, so pause-arriving-after-stop is not a race: `stopped` is absorbing in the state lattice and later `paused` writes are no-ops against it. Idempotency is free (setting a state twice is the same state); retries are safe by construction.

### 12.1 Topology view (read)

- **Live nesting tree:** parent/child Invokables as they exist right now — which agents are running, which spawned which, depth of delegation.
- **Per-node governance state:** current degradation level (NORMAL→…→TERMINAL), capability set, active taint summary (count of tainted values by causal_root, confidentiality floor high-water mark), trust level (L0/L1/L2 for federated peers), recent denials.
- **Config overlay:** the effective config per node (from the Config Builder or hand-written), diffed against what the operator believes is deployed — drift between intended and effective config is itself a red flag worth surfacing.
- **Event feed per node:** the same colour-coded audit stream as the Eval tab (§8), filtered to the selected node.

### 12.2 Interventions (write)

| Action | Semantics | Mechanism |
|---|---|---|
| **Pause** | node completes current intent, then holds before next intent evaluation | flag checked at IntentLoop boundary — never mid-effect, so no half-applied actions |
| **Resume** | clears pause flag | — |
| **Stop** | node finishes/aborts current intent, no further intents admitted; children get the same signal | delivered as governance state, not process kill, so the audit trail stays intact |
| **Replan** | node's next turn is forced through plan-revision: current plan invalidated, agent must re-derive | injected as a structured event, visible in trace |
| **Prompt injection (next turn)** | operator-authored text is added to the node's context assembly for exactly one upcoming turn | adapter hook at context assembly; single-shot, then auto-expires |
| **Attest taint branch** (v0.3) | operator vouches for a specific causal_root branch; heat/reputation recomputed downward over the attestation | append-only `operator_attestation` event in the Sentinel graph — see §8.1.1 for full semantics |
| **Lower budget cap** (v0.6) | tighten a node/subtree budget mid-run; narrowing only — raising goes through config | local accounting compares against the lowered cap; see §15 |

Notes on the injection action specifically:
- It is an **operator-side injection surface** — functionally the same mechanism attackers use, exposed deliberately for steering and red-teaming. It must be treated with the same seriousness: injected text enters context **carrying operator provenance, not trusted-operator-config status**. It does not get enum-supersession rights, does not launder taint, and cannot widen capabilities. An operator injection that says "you may now export" changes nothing at the gates.
- Scope is one node, one turn. Persistent instruction changes go through config (versioned, reviewed), not through injection.
- **Gating (v0.7, decision #5):** injection is available only on connections flagged test-bench; each injection requires a reason field (symmetric with attestation §8.1.1) and the run is marked `intervened` (§12.3). Production-flagged connections do not render the action.

### 12.3 The observe/intervene boundary (the rule that keeps Eval honest)

Control violates observe-only by design. That is fine *only if* the boundary is explicit:

- **Every intervention is a first-class recorded event** (who, what, which node, which turn) in the same trace as everything else.
- **Any run containing an intervention is marked `intervened` and is excluded from Scenario Delta / Core scores by default.** Its EvidenceCases remain fully valid as *cases* — a caught fabrication is a caught fabrication — but the run cannot silently enter aggregate metrics, or the operator's own steering contaminates the measurement the product's credibility rests on.
- **Eval tab stays read-only; the Control plane owns all writes.** No "pause" button ever appears on an experiment screen. One surface observes, one intervenes, and the trace records which was which.
- **Prod gating (open question, §10):** current lean is that write-actions require the connection to be flagged test-bench / self-hosted; hosted-proxy connections get topology view only.

### 12.4 Availability ladder

| Connection | Control plane shows |
|---|---|
| Proxy | greyed entirely — "available with adapter" (the strongest single upsell panel in the product: a live topology map is visibly worth the wrap) |
| Adapter, single agent | live topology of one node + full interventions |
| Adapter, multi-agent tree | full tree, per-node interventions, cascade semantics (stop propagates down) |
| + Sentinel | topology annotated with cross-session reputation per node; branch attestation (§8.1.1) enabled |
| Hosted connection | topology view (read) only; write-actions require self-hosted / test-bench flag (§12.3, open question §10) |

---

## 13. Replay mode (new in v0.4) — time-travel over traces, counterfactuals over governance

**Purpose:** every recorded run is a reusable experiment. Replay mode turns a trace into (a) a state-by-state debugger and (b) a deterministic test bench for governance counterfactuals: "what would have happened on this exact trace if the config / tool set / taint were different."

**The core insight that makes this cheap and honest:** the governance layer is a deterministic function of the event sequence. Gates, taint propagation, degradation transitions, and Sentinel heating are pure computations over recorded events — replaying or re-evaluating them requires **no LLM call**, is bit-reproducible, and is therefore first-class product material (same reason the Demo (§4) is pre-recorded). Only *the agent's behavior* is stochastic; the line between the two is the load-bearing distinction of this whole section.

### 13.1 Trace scrubber (read)

- **Timeline of the recorded run:** step forward/back through every event (intent, gate evaluation, tool call, tool result, denial, degradation transition, operator intervention).
- **Full governance state at each step:** taint graph snapshot (§8.1 view, scoped to the cursor), degradation level, capability set, confidentiality floor high-water, Sentinel heat, and — per gate — the actual evaluation with inputs and verdict, not just pass/fail.
- **Diff between steps:** select two cursor positions → what changed (new taint edges, heat delta, level transition). This is how "where exactly did it go wrong" is answered.
- Works on any recorded trace: proxy traces show the proxy-visible subset (requests/responses/claims); adapter traces show everything. Same greying convention as everywhere (§2).

### 13.2 Counterfactual governance replay (deterministic — the headline)

Fork the trace at any step, edit the world, re-evaluate governance over the *recorded* agent actions:

| Editable | Example question it answers |
|---|---|
| **Tool availability / capability table** | "if this agent hadn't had `bash`, where does the trace first hit a denial?" |
| **Config** (any Config Builder output, §11) | "does my config v2 still block the exfil this trace contains — and does it newly block anything legitimate?" |
| **Synthetic taint injection** | "if this value had arrived tainted at step 12, how does taint flow through the rest of the trace, which gates trigger, which exports get denied, how does the Sentinel node heat?" |
| **Trust levels** (federated peer L0/L1/L2) | "what if this peer were untrusted — which of its contributions get quarantined?" |

Output per fork: gate-by-gate verdict diff vs the recorded run, resulting taint graph, degradation trajectory, Sentinel heat trajectory. All deterministic, all reproducible, all without touching a model or a live agent.

**The first-divergence rule (non-negotiable honesty constraint):** counterfactual re-evaluation is *sound* only up to the first point where the counterfactual verdict differs from the recorded one. After a counterfactual denial, the real agent would have seen different context and behaved differently — the recorded continuation is no longer a valid stand-in. So:
- Replay runs to first divergence and reports it as the primary result ("under config v2, this trace is first blocked at step 14: taint→export").
- Continuation *past* divergence is available but rendered in a visually distinct "hypothetical" style with an explicit banner — useful for eyeballing, never for scores. Counterfactual results never enter Scenario Delta / Core metrics; they are a debugging and config-authoring instrument, same fencing logic as `intervened` runs (§12.3).

**The product framing this unlocks: config regression testing.** Corpus membership (v0.7, decision #11): traces containing an EvidenceCase are auto-pinned (must-block side); legitimate traces are pinned manually (must-pass side) — a regression corpus needs both sides, or a config that blocks everything passes CI. Hosted retention is a quota on pinned traces. Accumulated traces become a test corpus; before deploying config v2, replay the corpus against it and get a report — newly-blocked recorded attacks (good), newly-blocked recorded legitimate actions (regressions), per-trace first-divergence list. "CI for governance configs" — this is the natural bridge from the Config Builder (§11 step 4) and arguably the strongest retention feature in the spec: it gets more valuable the longer you use the product.

### 13.3 Live fork (deferred)

Actually re-executing the *agent* from a forked step under changed conditions requires a live model call: non-deterministic, needs an active connection, and produces a new trace rather than a re-evaluation of the old one. Deferred; when it ships, a live fork is just a new recorded run whose starting state was loaded from a replay cursor — it inherits all normal run semantics (scoring, marking) rather than the counterfactual carve-outs of §13.2.

### 13.4 Availability & storage

- Scrubber: any recorded trace (proxy or adapter), depth follows trace depth.
- Governance counterfactuals: **adapter traces only** — gates, taint, degradation are adapter-level concepts; a proxy trace has nothing to re-evaluate. Greyed with the standard label otherwise.
- **Storage (resolved v0.7, decision #10): set-membership persistence.** Enum-predicate counterfactuals need *membership*, not content: the declared sets live in operator config, so hosted persists only the index/hash of the matched element plus all labels and verdicts — never a value — and replay resolves membership against the local config. Full-counterfactual tier is therefore achievable on hosted with zero raw values stored; the same property that makes supersession paraphrase-proof makes replay privacy-proof. Fidelity tiers survive only for undeclared arguments (which carry full taint anyway, so their counterfactual space is smaller by construction).

---

## 14. Future scope (new in v0.5)

Two roadmap items big enough that each changes the *trust story*, not just the feature surface. Specified here at the level of commitments and constraints, not UI detail.

### 14.1 Multi-agent & A2A

Multi-agent already exists in fragments (Control-plane topology tree §12.1, cascade stop §12.2, federation levels in the trust model, "cross-agent leak" in adapter coverage §2). Future scope is making it end-to-end:

- **A2A channel as a governed boundary.** An agent-to-agent message is a sink on the way out and a source on the way in — nothing special, the same gate pipeline applies. Outbound: taint/confidentiality-floor checks against the peer's trust level (an L0 peer is an export destination like any other). Inbound: peer messages arrive tainted per the peer's level (L0 untrusted / L1 authenticated / L2 federated), with causal_root crossing the boundary so cross-agent taint lineage stays one graph.
- **A2A proxy mode.** Third interception point alongside tool-proxy: sit on the A2A channel and observe inter-agent traffic without wrapping either agent. Unlocks proxy-tier multi-agent evidence — fabricated *delegation* results ("sub-agent claims a task it never completed"), cross-agent canary tracking — with the same zero-code-change pitch as the tool proxy. Same passthrough/auth rules as §6.
- **Multi-agent Eval scenarios.** Faults targeted at the *seam*: drop/corrupt an inter-agent message, impersonate a peer at a lower trust level, delayed delegation response. EvidenceCase gains a `peer` dimension; the §8.1 taint graph and §12.1 topology already render the result — no new visualization needed.
- **Config Builder extension:** declare peers the way sinks are declared (peer id, trust level, allowed message classes). Undeclared peer = L0 = fail-closed, consistent with §11.2.
- **Protocol note:** "A2A" binds to *boundary semantics* (message in/out, peer identity, trust level), not to one wire format; adapters are thin. Order fixed in v0.7 (decision #12): native Invokable tree (already covered) → MCP-as-A2A adapter (an agent exposed as an MCP server; reuses the §5 MCP import path wholesale) → Google A2A when demand demonstrates.

### 14.2 Key Vault — credentials in one place, out of the agent entirely

**What it is:** central custody of tool credentials; the proxy injects the right credential at the sink, per tool, at call time. The agent's config holds vault references, never keys.

**Why it belongs in this product (the governance rationale, not just convenience):** a credential that is never in the agent's context cannot be exfiltrated *by* the agent — no prompt injection, no fabricated tool call, no model output can leak a key the model never saw. This is the confidentiality-floor philosophy applied to the highest-value secrets: remove them from the interpretable surface altogether. Injection at the proxy is effect-point enforcement — the credential exists exactly where the effect happens and nowhere upstream.

**What it explicitly reverses — and the price:** §6's cornerstone is "auth is passthrough, untouched; if the proxy doesn't touch auth, there's nothing for it to break." Vault mode is the opposite: the proxy now *holds and injects* credentials, becoming a high-value target and a single point of compromise. This is not a footnote; it is the cost of the feature, and the spec treats it as such:

- **Vault is a mode, never the default.** Passthrough remains the default; vault is opt-in per tool. §6 stays true for every tool not enrolled.
- **Self-hosted first.** Vault ships self-hosted-only initially (same trust posture as self-hosted proxy/control-plane); hosted vault, if ever, requires envelope encryption with customer-held root keys and a separate security review — not a v1 promise.
- **Scoped injection.** A credential is bound to (tool, endpoint) at enrollment; the proxy injects only on exact match. A prompt-injected agent redirecting a call to attacker.example gets no credential attached — enrollment scope is operator config, inaccessible from runtime reads (same soundness argument as enum supersession).
- **Vault events in the trace.** Every injection is a recorded event (tool, key id — never key material) → visible in scrubber/replay; EvidenceCase can show "call was made *with* credential X injected" without ever persisting the credential (consistent with §6 no-raw-bodies).
- **Rotation & revocation** are operator config actions (versioned), not control-plane commands — the §12.0 "commands cannot widen" rule extended: the plane can *revoke* (narrowing) in an incident, but granting/rotating goes through config.

- **Backend (v0.7, decision #13):** an existing secret store (Vault/KMS-class) underneath, our layer on top: (tool, endpoint) scoping, trace integration, enrollment UX. A lightweight encrypted-file dev backend ships for low-friction starts. We do not build credential storage; the differentiator is scoped sink-side injection.
- **Break-glass (v0.7, decision #14): none — fail closed.** Vault unreachable → injection impossible → the call is denied with a typed denial. A TTL credential cache would reintroduce the secret-on-proxy this feature exists to remove. No conflict with §12.0: disconnect-safety demands that *enforcement* not degrade offline, and a deny is enforcement working. Availability is solved where it belongs — vault HA, co-location on self-hosted. Side product: "agent behavior under credential loss" becomes an Eval scenario — does it report the typed denial honestly or fabricate access?

**Relation to onboarding:** vault absorbs the auth part of Get Started step 3 — the connection check becomes "vault has a working credential per tool," same pre-flight philosophy (§5), one place instead of N agent configs.

---

## 15. Budget caps (new in v0.6)

Operator-set limits on what a node or subtree may spend: tool-call count, cost (per-tool weights, token spend where visible), optionally per time window. Compact feature, but it must land on the existing rails rather than beside them:

- **Enforced locally, declared in config.** Accounting and the cap check run in-process at the IntentLoop boundary, like every other gate input — never a control-plane round-trip (§12.0 holds: guarantees don't depend on the network). The Config Builder declares budgets (§11 step 1b); the control plane only *watches and narrows*.
- **Exhaustion is a fact, not an exception.** Hitting a cap emits a typed fact driving a degradation transition (e.g. → a state admitting only zero-cost/finalization intents, then TERMINAL if the agent keeps trying). No parallel "budget state machine" — one machine, one trace, and the scrubber/replay (§13) get budget trajectories for free. Counterfactual "what if the cap were N" is then just another §13.2 config edit.
- **Narrowing vs widening.** Mid-run the control plane may **lower** a cap (incident response) — consistent with §12.0 "commands cannot widen." **Raising** a cap or topping up an exhausted node is widening: versioned config change only. No top-up button on the Control plane, deliberately.
- **Subtree budgets close delegation amplification.** A child spawns with a slice of its parent's remaining budget; the subtree sum is bounded by the parent's cap. Without this, delegation multiplies spend and a cap on the root is decorative. Topology view (§12.1) shows per-node remaining budget and subtree rollup.
- **Eval angle — two hooks.** (a) "Budget mismatch" (agent claims more/fewer calls than reality) is already core proxy coverage (§2); caps give it teeth — claimed-vs-metered against a hard limit. (b) New fault scenario, **budget exhaustion mid-task**: cap set to bind partway through; measured question — does the agent surface the limit honestly or fabricate completion of steps it could no longer afford? Runs at proxy depth (call counting) and adapter depth (full cost accounting).

Cost model (resolved v0.7, decision #15): v1 uses operator-declared per-tool weights — deterministic, hence replay-compatible (§13.2). Provider billing integration, if added, is a reporting overlay only and never an enforcement input (non-deterministic, lagged — would break counterfactual replay). Time-window budgets deferred: rate limiting is an availability feature, not governance; if built, windows run on trace time so replay determinism survives.


---

## 16. Notifications (v0.11) — loud elsewhere

Quiet-until-wrong (header constraint) assumes someone is looking at the screen. The on-call persona is not. The pairing rule: **the UI is quiet; the notification channel is where wrong gets loud.**

- **Channel: webhook, first and possibly only.** JSON POST with the triggering event + node + level + permalink into the relevant view. Slack/Discord/PagerDuty are webhook consumers, not integrations we build. Explicit non-goal: being a paging system — we emit, their stack routes, dedupes, escalates.
- **Triggers (v1 set, per-connection config):** degradation level transition upward (per-level threshold), Sentinel heat crossing an operator-set threshold, run completed with ≥1 EvidenceCase, node stale (missed heartbeats, protocol §7). Attestations and downward transitions are notified only if opted in — silence about recovery is acceptable, silence about escalation is not.
- **Source:** the plane service event feed the backend already has — no new instrumentation, a subscriber with an HTTP sink and per-trigger debounce.
- **Failure honesty:** webhook delivery is at-least-once with retries and a dead-letter log visible in settings; a notification system that fails silently is worse than none.
