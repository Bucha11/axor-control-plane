# Axor ↔ EU AI Act — one-pager (inbound compliance asset)

Positioning: Axor is **evidence and control infrastructure** for agentic AI
systems. It does not make a system compliant by itself; it produces the
artifacts Articles 12/14/26 ask operators to produce. Not legal advice.

| AI Act obligation | What Axor produces |
|---|---|
| **Art. 12 — Record-keeping**: high-risk systems must automatically log events over their lifetime | Append-only kernel event log (every tool intent, verdict, value provenance); alembic-migrated store; retention window (`AXOR_RETENTION_DAYS`); export as HTML/PDF receipts |
| **Art. 14 — Human oversight**: effective oversight incl. ability to intervene or interrupt | Live Control plane: pause / budget-cap / stop / cascade-stop per node & subtree; Ed25519-signed operator commands (attributable interventions); reason-required attestations, append-only |
| **Art. 26 — Deployer obligations**: monitor operation, keep logs, suspend on risk | node-stale + degradation webhooks (with dead-letter honesty); EvidenceCase on caught deviations; kill-switch = cascade-stop |
| **Art. 15 — Accuracy & robustness**: resilience, testing | Fault-injection eval (tool deprivation catalog); two-sided regression corpus = documented pre-deployment testing of policy changes |
| **Art. 79 / market surveillance requests** | Deterministic replay: hand the authority the exact recorded run and re-derive every gate decision — same pure kernel that enforced |
| **GPAI transparency (Art. 53)** | Out of scope — Axor governs deployment-side execution, not model training |

Honest boundaries: Axor covers the *execution/tool* surface of an agent, not
model outputs as such; classification of your system's risk tier is yours;
logs are only as complete as the surfaces you route through Axor
(proxy-observed or adapter-governed).

CTA: "Send us your Article 12/14 checklist — we'll map each line to a screen
or an export in a 30-minute call."
