// Pricing (monetization spec): safety is free forever; you pay only for how your
// ORG runs the plane. Three tiers, static — no backend. Team is the typical first
// paid step, marked with a subtle steel border.
import { C, MONO } from "../theme";

interface Tier {
  name: string;
  price: string;      // the "price shape", not an invoice
  priceNote?: string;
  who: string;        // one-line "for"
  features: string[];
  free: boolean;      // whether the tier itself is free
  paidNote: string;   // free-vs-paid line
  highlight?: boolean;
}

const TIERS: Tier[] = [
  {
    name: "Free",
    price: "$0",
    priceNote: "open source",
    who: "individuals · small teams · research/academic (incl. EE)",
    features: [
      "proxy + fault injection + EvidenceCase receipts",
      "replay + counterfactuals",
      "Config Builder",
      "plane service: pause/stop/replan/inject (single operator)",
      "attestation (single-operator + reason)",
      "regression corpus (local, unlimited)",
      "notifications (webhook)",
      "EvidenceCase export (PDF/link)",
      "topology (per connection)",
      "Vault mechanism + dev backend",
    ],
    free: true,
    paidNote: "free — safety is free forever",
  },
  {
    name: "Team",
    price: "$50–100 / node · mo",
    priceNote: "floor ~$500/mo · hosted or self-hosted, same price",
    who: "first company deployments · 5–30 nodes",
    features: [
      "everything in Free, plus:",
      "hosted convenience + license",
      "scheduled corpus CI + history",
      "notification routing rules / per-team channels",
    ],
    free: false,
    paidNote: "paid — the typical first paid step",
    highlight: true,
  },
  {
    name: "Enterprise",
    price: "annual contract",
    priceNote: "node bands + support · from $20–50k/yr",
    who: "SSO/SAML/SCIM · RBAC · air-gapped fleets",
    features: [
      "everything in Team, plus:",
      "SSO/SAML/SCIM · RBAC",
      "compliance report generator (audit-ready period reports:",
      "  interventions, attestations w/ reasons, denial stats)",
      "fleet view (all agents across teams, cross-connection search)",
      "air-gapped deployment",
      "managed retention / legal hold / audit log export",
      "SLA + private channel",
    ],
    free: false,
    paidNote: "paid — annual",
  },
];

export default function Pricing() {
  return (
    <div style={{ maxWidth: 860, margin: "0 auto" }}>
      <h1 style={{ fontSize: 26, fontWeight: 700, lineHeight: 1.25, margin: "0 0 8px" }}>
        Pricing
      </h1>
      <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.dim, marginBottom: 28, lineHeight: 1.6 }}>
        Line 1 — anything that makes an agent safer is free forever.
        <br />
        Line 2 — you pay only for how YOUR ORG runs it.
      </div>

      <div className="flex gap-4" style={{ flexWrap: "wrap" }}>
        {TIERS.map((t) => (
          <div
            key={t.name}
            className="flex flex-col p-4"
            style={{
              flex: "1 1 240px",
              minWidth: 240,
              background: C.panel,
              border: `1px solid ${t.highlight ? C.steel : C.line}`,
              borderRadius: 10,
            }}
          >
            <div className="flex items-center justify-between mb-2">
              <div style={{ fontFamily: MONO, fontSize: 14, fontWeight: 700, color: C.text }}>
                {t.name}
              </div>
              <span
                style={{
                  fontFamily: MONO,
                  fontSize: 9.5,
                  color: t.free ? C.green : C.steel,
                  border: `1px solid ${t.free ? C.green : C.steel}`,
                  borderRadius: 20,
                  padding: "2px 8px",
                }}
              >
                {t.free ? "free" : "paid"}
              </span>
            </div>

            <div style={{ fontFamily: MONO, fontSize: 15, color: C.text }}>{t.price}</div>
            {t.priceNote && (
              <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 2 }}>
                {t.priceNote}
              </div>
            )}

            <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, margin: "12px 0", lineHeight: 1.5 }}>
              for: {t.who}
            </div>

            <div style={{ borderTop: `1px solid ${C.line}`, paddingTop: 12, marginTop: "auto" }}>
              {t.features.map((f, i) => (
                <div
                  key={i}
                  className="flex gap-2 mb-2"
                  style={{ fontFamily: MONO, fontSize: 11, color: C.mut, lineHeight: 1.4 }}
                >
                  {!f.startsWith(" ") && !f.endsWith("plus:") && (
                    <span style={{ color: C.dim }}>·</span>
                  )}
                  <span style={{ color: f.endsWith("plus:") ? C.dim : C.mut }}>{f.trim()}</span>
                </div>
              ))}
            </div>

            <div
              className="mt-3"
              style={{
                fontFamily: MONO,
                fontSize: 10.5,
                color: t.free ? C.green : C.steel,
                borderTop: `1px solid ${C.line}`,
                paddingTop: 10,
              }}
            >
              {t.paidNote}
            </div>
          </div>
        ))}
      </div>

      <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim, marginTop: 24, lineHeight: 1.6 }}>
        priced per governed node — ephemeral nodes count by concurrent peak, not by spawn.
      </div>
    </div>
  );
}
