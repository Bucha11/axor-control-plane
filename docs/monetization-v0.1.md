# Axor Control Plane — Monetization (v0.1): Open Core + Enterprise License

> Canonical packaging, tiers, and prices are defined in **axor-packaging.md** (in the axor-lab repo, `docs/design/`). If this document conflicts with it, axor-packaging.md wins. This doc is the open-core reasoning; packaging is the price list.

Model: everything an individual needs is open source; organizations pay for a license that unlocks organizational features — on our hosting or theirs. Payment is for the license, support, and compliance surface, never for hosting per se and never for safety.

---

## 1. The two lines that define everything

**Line 1 — ethical (what is never paid):** anything that makes an agent *safer* is free forever. Fail-closed defaults, all gates, e2e command signatures, single-operator attestation, fault scenarios, EvidenceCase capture, replay. A security product that ransoms safety features loses the community whose trust the paper is meant to buy. This line is permanent and public.

**Line 2 — commercial (what is paid):** anything whose value appears only when *an organization* uses the product. Multiple operators, fleets of agents, proof for auditors, integration with corporate identity. Rule of thumb: if the feature answers "how do WE run this," it's paid; if it answers "is MY agent safe," it's free.

The spec already drilled the holes on this line: §9 deferred "team features, SSO, history → monetization layer"; decision #8 left the multi-operator attestation policy as a hook; decision #11 put corpus quotas on hosted. Nothing needs re-architecting.

## 2. Feature split (mapped to spec)

| Capability | Free (OSS) | Paid |
|---|---|---|
| Proxy, fault injection, Eval receipts (§4–8) | ✅ all | — |
| Replay + counterfactuals (§13) | ✅ | — |
| Config Builder incl. code-in-wrapped-out (§11) | ✅ | — |
| Plane service: pause/stop/replan/inject, single operator (§12) | ✅ | — |
| Attestation, single-operator + reason (#8) | ✅ | multi-operator policy (require N confirmations), org keysets |
| Regression corpus (#11) | ✅ local, unlimited | hosted quota lift; **scheduled corpus CI + history** |
| Notifications (§16) | ✅ webhook | routing rules, per-team channels |
| EvidenceCase export (§8.3) | ✅ PDF/link | **compliance report generator** (period reports: interventions, attestations w/ reasons, denial stats — audit-ready) |
| Topology (§12.1) | ✅ per connection | **fleet view**: all agents across teams, cross-connection search |
| Identity | local tokens, GitHub OAuth | SSO/SAML/SCIM, RBAC (viewer/operator/admin) |
| Retention | local files, yours | managed retention policies, legal hold, audit log export |
| Vault (§14.2) | ✅ mechanism + dev backend | fleet key management, rotation policies at scale |
| Support | community | SLA, private channel, upgrade assistance |

Two deliberately strong paid anchors: **compliance reports** (EvidenceCase + append-only attestation log + signed operator actions are audit artifacts already — the generator is cheap to build, priced on value to the buyer, EU-AI-Act-shaped demand) and **fleet view** (the moment a company runs 20+ governed agents, the free per-connection topology stops scaling organizationally — natural, non-artificial ceiling).

## 3. Licensing structure

- **Ecosystem packages** (axor-core, axor-eval, axor-probe, axor-sentinel) and the kernel: **Apache-2.0**, unconditionally. These underpin the paper; any license cleverness here costs academic credibility.
- **Platform repo:** Apache-2.0 with an `/ee` directory under a commercial license (GitLab pattern). EE code is source-visible (security buyers audit everything) but not free to use in production. Alternative considered — FSL/BSL on the whole plane service — rejected: it taints the "advisory overlay, self-hostable" trust story and complicates the paper's artifact release.
- **Cloud-provider strip-mining risk** (AWS hosting your OSS): real for Grafana-scale, negligible here for years. Revisit only on evidence; don't pre-pay for it with a worse license today.

## 4. License mechanics for paid self-hosted

- **Ed25519-signed license file** — the same signature infrastructure the control plane already ships (protocol §6). Offline-verifiable: no phone-home, no license server, works air-gapped. For this audience, "our license check is the same crypto that guards your command channel, and it never calls us" is itself a selling point.
- License encodes: org, tier, node ceiling, expiry. EE features check it at startup; expiry degrades gracefully to read-only EE (never disables safety features — Line 1).
- Optional, off-by-default usage telemetry. Security buyers assume phone-home; being provably free of it differentiates.

## 5. Tiers & pricing sketch

| Tier | Price shape | For |
|---|---|---|
| **Free** | $0, OSS | individuals, small teams, research/academic (explicit carve-out: research use of everything incl. EE, aligned with §7 neutrality) |
| **Team** | per environment/mo (order of ~$250/env: dev / staging / prod); hosted or self-hosted, same price | first company deployments |
| **Enterprise** | annual contract, node bands + support; realistic entry $20–50k/yr | SSO, compliance reports, fleet view, air-gapped, SLA |

Metric — split by tier, deliberately:

- **Team is priced per environment** (a stable, countable unit — a deployment of
  the plane: dev / staging / prod). This is the credit-card tier, so the metric
  must be un-arguable at signup. "Per governed node" fails that test *early*: a
  buyer with delegation trees and ephemeral workers cannot predict the bill, and
  "what counts as a node / concurrent vs registered / dev vs prod" becomes a
  sales conversation before the first dollar. An environment is something they
  already know they have.
- **Per-node bands are reserved for Enterprise**, where fleet topology is
  actually modeled and the buyer thinks in nodes. There, ephemeral nodes count
  by concurrent peak, not by spawn (else delegation trees get taxed — same
  amplification problem budget caps solved, same fix).

Rejected: **per-EvidenceCase / per-run** metrics. Charging for each caught
discrepancy is a perverse incentive — it taxes the product working, tempts
tolerance of false positives, and gives the user a reason not to open cases.
For a product whose whole trust rests on "we only flag real fabrications," that
is exactly the wrong thing to meter.

## 6. Motion (solo-founder realistic)

1. **Now → paper:** everything free; revenue via 3–5 paid design partnerships (implementation + config authoring + a red-team pass using the product). Buys runway, feedback, logos. The paper is the lead-gen into exactly the security orgs that become Enterprise buyers.
2. **First 10 external proxy users:** stand up Team tier (hosted convenience + license). Pricing page exists from day one even while everything is free — anchoring, and it signals the project intends to survive, which enterprises require before adopting.
3. **First compliance question from a prospect** (it arrives earlier than expected): build the report generator into Enterprise, close the design partners into contracts.

## 7. Risks, named

- **Feature-line pressure:** every new feature triggers a free-or-paid fight with yourself. Mitigation: Lines 1–2 are written down (this doc); a feature that doesn't clearly answer "how do WE run this" defaults to free.
- **Open-core resentment:** some community friction is inherent. Mitigation: Line 1's absolutism, EE source visibility, research carve-out.
- **Solo enterprise sales:** the real bottleneck — cycles are long and meetings are many. Mitigation: design-partner motion first (they pre-commit), paper-driven inbound, and pricing that doesn't require procurement below Enterprise (Team tier is credit-card).
- **Conflict with §7 neutrality:** the academic artifact must not read as an ad. Mitigation: research carve-out + the artifact ships from the Apache-2.0 ecosystem packages, not the platform.


---

## 8. Implementation status (2026-07-05)

The license mechanics of section 4 are implemented in `packages/axor-backend/`:
`axor_backend.ee.license` (Ed25519-signed license files, offline-verifiable via
the same PyNaCl stack the plane commands use — no phone-home), and the `/ee`
subtree marks the source-visible commercial boundary. Endpoint:
`POST /v1/license/verify`. Line 1 is enforced structurally: nothing under `ee/`
touches a gate, a taint decision, or a fail-closed default, and expiry degrades
EE to read-only rather than disabling any safety feature.
