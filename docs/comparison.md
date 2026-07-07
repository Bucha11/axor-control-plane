# Where Axor sits (honest comparison, no FUD)

Three tool families get conflated. They compose — most teams should run an
observability stack AND guardrails AND (we argue) execution governance.

| | Observability (LangSmith, Langfuse, Helicone) | I/O guardrails (NeMo Guardrails, Guardrails AI, Lakera) | **Axor (execution governance)** |
|---|---|---|---|
| Question answered | "what did the agent do?" | "is this text acceptable?" | "may this intent cross into this tool — and can I prove what happened?" |
| Acts at | trace collection, after the fact | prompt/response boundary | intent→tool boundary, per value |
| Failure artifact | a trace to read | a block/rewrite | an **EvidenceCase** (reproducible receipt, share/PDF) |
| Replay | view the trace | — | **deterministic re-gating** + counterfactuals, no model call |
| Live control | — | — | pause / budget-cap / cascade-stop over a fleet, signed commands |
| Regression for policy changes | — | test suites for filters | two-sided corpus CI (must-block + must-pass) |
| Catches "agent lied about a tool" | only if you read the trace | no (it checks text, not tool reality) | yes — claim vs observed reality, deterministic |

What the others do better, honestly: observability tools have far richer
LLM-call analytics (tokens, costs, prompt diffs) — Axor doesn't try; guardrails
catch toxic/off-policy *text*, which Axor doesn't look at. Run them together:
observability for insight, guardrails for content, Axor for execution and proof.
