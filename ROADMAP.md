# Roadmap (honest edition)

What's shipping vs what's an idea. Updated with releases; no dates promised.

**Now (shipped)**: eval → EvidenceCase (share/HTML/PDF) · deterministic replay
+ counterfactuals · taint/provenance graph · live Control (pause/stop/budget/
cascade) · two-sided regression CI · MCP onboarding (HTTP + stdio servers, the
proxy spawns stdio gateways) · streaming passthrough · webhook notifications +
dead-letter · scoped API keys · alembic migrations · retention · concurrent
runs per proxy · offline EE license · **Team (EE): scheduled corpus CI + run
history, notification routing (channels + node globs)** · coding agents
(Claude Code / Codex hooks, developer seats, seat cap, agent trees and
subagents) · **Business (EE): single sign-on (OIDC + SAML 2.0 with signed
requests, encrypted assertions and IdP-initiated sign-in), SCIM 2.0 with
group→role mapping, session limits, per-capability permissions, full audit
trail with export, retention and monthly report** · air-gapped install (one
offline bundle: images, stack, installer; your own certificate or a local CA)
· **Enterprise (EE): the compliance pack — any period of the whole governed
surface as one signed document — and legal hold**.

**Next**: OpenAI Agents SDK + CrewAI wrap recipes ·
landing + docs site · benchmark harness (catch-rate table) · load-ceiling doc.

**Later (needs demand)**: hosted multi-tenant · HA backend · private
benchmark registry · SLA and a support process · fleet view across
deployments.

**Non-goals**: prompt filtering (we govern execution, not text) · a pager (we
emit webhooks; PagerDuty is a consumer) · blocking at the proxy (enforcement
lives in the adapter — the proxy observes).
