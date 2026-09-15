# Roadmap (honest edition)

What's shipping vs what's an idea. Updated with releases; no dates promised.

**Now (shipped)**: eval → EvidenceCase (share/HTML/PDF) · deterministic replay
+ counterfactuals · taint/provenance graph · live Control (pause/stop/budget/
cascade) · two-sided regression CI · MCP onboarding (HTTP + stdio servers, the
proxy spawns stdio gateways) · streaming passthrough · webhook notifications +
dead-letter · scoped API keys · alembic migrations · retention · concurrent
runs per proxy · offline EE license · **Team (EE): scheduled corpus CI + run
history, notification routing (channels + node globs)**.

**Next**: OpenAI Agents SDK + CrewAI wrap recipes ·
landing + docs site · benchmark harness (catch-rate table) · load-ceiling doc.

**Later (needs demand)**: hosted multi-tenant ·
SSO/RBAC (Enterprise) · scheduled corpus CI + history (Team) · HA backend.

**Non-goals**: prompt filtering (we govern execution, not text) · a pager (we
emit webhooks; PagerDuty is a consumer) · blocking at the proxy (enforcement
lives in the adapter — the proxy observes).
