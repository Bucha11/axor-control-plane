# Launch post (draft) — "Your agent lies when its tools fail. Here's the receipt."

Target: HN (Show HN), r/LocalLLaMA, X thread. Hero: docs/demo.gif.

---

Show HN: Axor — catch your LLM agent lying about its tools, then replay it deterministically

Break one of your agent's tools on purpose — a timeout, a poisoned result —
and most agents don't report the failure. They **fabricate**: "Based on the
search results, rates rose 0.25%." Zero bytes came back.

Axor is an open-source (Apache-2.0) governance platform for agents:

1. **Catch.** An observe-only proxy sits in front of your tools (auth passes
   through byte-for-byte, raw bodies never stored). It injects a fault and
   compares what actually happened against what the agent claimed. The
   mismatch becomes an **EvidenceCase** — a reproducible receipt (HTML/PDF,
   revocable share link), not a score.
2. **Replay.** Every run replays deterministically — no model call. Fork a
   counterfactual ("no exec capability", "this value arrives tainted",
   "budget = N") and see the first step where the outcome diverges. A
   provenance graph traces where every value came from.
3. **Govern.** Wrap the agent in the axor-core runtime and it becomes a live
   node you can pause, budget-cap (decrease-only), or cascade-stop with its
   whole subtree. Operator commands are Ed25519-signed; the adapter verifies
   with its own keys — a compromised backend can't forge them. Axor down ⇒
   agent unaffected (advisory overlay).
4. **Regress.** Pin caught attacks (must-block) and legit flows (must-pass),
   replay the corpus under a candidate policy: deterministic CI for
   governance configs. A config that blocks everything fails honestly.

Demo is one click and zero credentials (`docker compose up`, mock tools,
scripted agent). MCP servers onboard by pasting the client config you already
have.

A word on the numbers below, because all-100% tables should make you
suspicious. These are **not** a field-accuracy claim. They're a determinism +
zero-false-positive proof of the *detector itself*, run against constructed
personas (100 seeded trials per cell, deterministic harness in axor-eval —
`python -m axor_eval.benchmarks.catch_rate`, no model calls). Read them as: when
a misbehaving persona fabricates, the detector catches it every time and never
flips on an honest one — a correctness property, reproducible on your machine.
Catch rates against **real LLM agents** (per-model rows, where the number will
be lower and we'll report it honestly) are the next milestone — swap a persona
for a live loop with the same harness.

| persona \ fault mode | silent_fail | corrupt_retrieval | instruction_injection | tool_substitution |
|---|---|---|---|---|
| misbehaving persona (structured claims) | 100% | 100% | 100% | 100% |
| misbehaving persona (free text only) | 100% (heuristic) | 100% | 100% | 100% (heuristic) |
| honest persona (false-positive check) | 0% | 0% | 0% | 0% |
| real LLM agents (per model) | _pending — reported when measured_ ||||

What it is NOT: not prompt filtering, not observability-only, not a sandbox.
We govern the execution boundary — which intents cross into which tools.

Stack: Python (FastAPI + a pure replay kernel), React/TS. Replay uses the
same kernel that enforces, so what you review is what ran. Everything above
is free forever; orgs pay for fleet features (open core, GitLab pattern).

— [name], solo. Paper in submission; happy to answer anything, especially
"what does the proxy see" (SECURITY.md has the threat model).
