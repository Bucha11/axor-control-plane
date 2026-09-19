# Roadmap (honest edition)

What's shipping vs what's an idea. Updated with releases; no dates promised.

**Now (shipped)**: eval → EvidenceCase (share/HTML/PDF) · deterministic replay
+ counterfactuals · taint/provenance graph · live Control (pause/stop/budget/
cascade) · two-sided regression CI · MCP onboarding (HTTP + stdio servers, the
proxy spawns stdio gateways) · streaming passthrough · webhook notifications +
dead-letter · scoped API keys · alembic migrations · retention · concurrent
runs per proxy · offline EE license · **Team (EE): scheduled corpus CI + run
history, notification routing (channels + node globs)** · multi-agent: taint
across hops, containment two-tree view, topology lens, federation peers ·
behavioral drift (axor-probe battery → graded case) + explicit self-heal ·
cross-session reputation snapshots (axor-sentinel) · credential vault
(sink-side dispense, node-signed fetches, envelope mode) + signing vault ·
axor-lab import/export · human login (`axor-identity`: users, orgs, roles) and
per-org tenant scoping · framework recipes (OpenAI Agents SDK, CrewAI) ·
`axor-proxy run` for CLI agents · measured load ceilings (docs/ops-limits.md) ·
landing page (`site/`, deployed by the Pages workflow).

**Next**: benchmark harness (catch-rate table) · a docs site beyond the landing
page · hosted trial.

**Later (needs demand)**: hosted multi-tenant (the code is tenant-scoped; the
*offering* is not) · SSO/OIDC (Enterprise — `axor-identity` does passwords +
roles today) · HA backend (single-instance is a documented limit).

**Non-goals**: prompt filtering (we govern execution, not text) · a pager (we
emit webhooks; PagerDuty is a consumer) · blocking at the proxy (enforcement
lives in the adapter — the proxy observes).
