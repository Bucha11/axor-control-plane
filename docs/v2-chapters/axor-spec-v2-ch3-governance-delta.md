# Axor Control Plane — Spec v2 (draft), Chapter 3: Governance Delta under Multi-Agent

Extends §7 of ui-spec v0.13. In single-agent, "governance delta" is one number: same agent, same fault, integrity with vs without axor-core. In a tree that number fractures — a fault injected at one node produces effects (denial, fabrication, containment) at *other* nodes, at other depths, some turns later. This chapter defines what is measured, where, and — the honest part — what must NOT be collapsed into a headline.

Inherits §7's split intact: **(A) enforcement on/off** is the clean, demoable claim; **(B) governance changes behavior** is the subtle research observation. Multi-agent widens the gap between them, so the discipline matters more, not less.

---

## 1. Why one number breaks

A fault at a leaf node in a tree has three distinct governance effects, and they are not the same measurement:

1. **Local** — did the node where the fault landed fabricate or report honestly? (This is the §7 single-agent question, unchanged, per node.)
2. **Propagated** — did the fabrication/taint travel up or across to other nodes, or was it contained at a boundary? This is the effect that *only exists* in multi-agent, and the one governance most distinctively addresses (carried taint, §ch1; boundary denials).
3. **Systemic** — did the *whole task* succeed honestly, fail honestly, or fail by fabrication? The end-to-end outcome the operator actually cares about.

Reporting a single averaged delta over a tree hides which of these moved. A governed run can improve containment (2) dramatically while local honesty (1) is unchanged and systemic outcome (3) flips from "confident wrong answer" to "honest failure" — three different stories, one useless average.

---

## 2. What gets measured, and where

**Per-node integrity (local).** The §7 metric, computed at each node independently. A tree yields a *vector* of per-node integrities, not a scalar. The Eval tab renders it as the topology (§12.1) colored by integrity, not as a single bar.

**Containment (propagated) — the multi-agent-native metric.** For a fault whose effect is tainted/fabricated content, measure whether it crossed each boundary it reached:
- `contained_at`: the node/edge where propagation was denied (governed) or NULL (escaped).
- Ungoverned twin: where did the same content actually reach? (Usually: the export sink, or the root's final answer.)
- Containment delta = boundaries-held / boundaries-reached. This is new; §7 had no propagation axis because there was nothing to propagate to.

**Systemic outcome (end-to-end).** A three-way label on the whole run, not a number:
- `honest_success` · `honest_failure` (surfaced the problem, didn't complete) · `fabricated_failure` (completed by lying).
- The governance claim in its strongest honest form: **governance converts `fabricated_failure` → `honest_failure`.** It does not manufacture success — saying so would be the §7(B) overreach at tree scale. An agent denied a tainted export doesn't thereby get the right answer; it gets an honest non-answer. That is the win, stated without inflation.

---

## 3. The two-trace object, generalized

§7's side-by-side (ungoverned lies left, governed caught right) becomes a **two-tree** object: the same fault replayed over the topology with and without axor-core, aligned on the fault-injection point. The EvidenceCase gains:
- the `contained_at` boundary marked on the governed tree where the ungoverned tree shows continued propagation — the multi-agent analog of §7's `intent_denied` marker, now placed at a *node boundary* rather than a single timeline point.
- per-node integrity diff, so the operator sees not just "caught" but "caught here, and here it was already honest, and here governance changed the context and behavior shifted (B — flagged, not counted)."

This is the strongest visualization the product has: cause at a leaf, containment at a boundary, honest failure at the root, all in one frame — and it is *only* possible because governance is a deterministic replay (§13), so both trees are re-derived from one recorded fault, not two separate stochastic runs.

---

## 4. The measurement trap, and the rule that avoids it

Multi-agent sharpens §7(B): under governance, upstream nodes receive *different context* (a denial instead of a fabricated result, compressed/taint-limited inputs) and therefore behave differently — cascading. Some of the delta is enforcement catching things (A, countable); some is behavior drift from altered context (B, real but not "enforcement caught it"). At tree scale these entangle across nodes.

**Rule: attribute at the boundary, not at the outcome.** A delta is counted as enforcement (A) only where a specific gate produced a specific denial on a specific edge — a discrete, verifiable event. Everything downstream of that denial (how the parent reacted, whether it then fabricated less) is behavior (B): shown, flagged, never summed into the enforcement claim. The containment metric (§2) is pure-A by construction — it counts boundary denials, which are events. Systemic outcome is A+B entangled and is therefore reported as a *label*, never as a governance-attributed number.

This is the §7 neutrality discipline (demo-hero vs research-caveated) made structural: the containment number is safe to headline because it counts events; the systemic label is safe because it's a label; per-node integrity is safe because it's the unchanged §7 metric. Nothing that entangles A and B is ever rendered as a single governance-caused figure.

---

## 5. Federation scope (ties to ch1)

- **Intra-federation:** the whole tree is one measurement domain — containment boundaries include lateral edges (ch1 §1), governance delta spans the federation.
- **Inter-federation:** measurement STOPS at the boundary. We measure containment *at* our edge to a foreign peer (did we deny sending a tainted value to an L0 peer — countable, ours). We do NOT measure the foreign agent's integrity — no label authority (ch1 §2.1), no visibility, and claiming a delta over an agent we don't govern would be exactly the conflict-of-interest §7 warns against. Foreign nodes are excluded from every per-node vector.

---

## 6. Decisions & open

- **Academic artifact:** the reproducible run reports the per-node integrity vector and the containment metric across models *without* axor-core as the neutral baseline; the two-tree governance comparison ships as a separate, labeled "effect of enforcement" experiment (§7 discipline, unchanged). Systemic-outcome labels are reported, never a systemic "score."
- **Demo:** the two-tree containment visualization (§3) is the hero — single fault, cascade contained — replacing the single-agent split as the lead asset once multi-agent ships.
- Open [verify-by-design]: containment denominator — "boundaries reached" needs the ungoverned twin to define reach; for a fault the ungoverned twin propagates infinitely (to the root), so reach is well-defined (every edge on the path to where it landed). Confirm this holds when propagation branches (fault reaches a fan-out node) — lean: reach = union of all edges the ungoverned taint touched, containment = fraction the governed tree denied.
