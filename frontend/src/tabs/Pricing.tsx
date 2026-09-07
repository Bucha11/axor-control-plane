// Pricing — canonical ladder from axor-packaging.md (SINGLE SOURCE OF TRUTH).
// ONE LADDER. Private Lab (the security / evidence workspace) and Control Plane
// (production enforcement) are one product sold on one ladder: a rung entitles
// both. They used to be a rung plus a separately-licensed add-on, which asked
// every buyer a question they could not answer before trying the thing — and
// the entitlement code carried a module flag that was signed, displayed, and
// decided nothing. Static, no backend. Safety and hobby-scale privacy are free
// forever; you pay for hosted collaboration, the security workflow, and the
// size of the governed fleet.
import { C, MONO } from "../theme";
import Coach from "../components/Coach";

// A feature is either shipping today or on the roadmap. We mark the difference
// explicitly rather than listing aspirational capabilities as if they exist — a
// pricing page that over-claims is the same dishonesty the product refuses
// everywhere else. `plus` is the "everything in X, plus:" divider.
interface Feature {
  text: string;
  roadmap?: boolean; // planned, not yet shipped — rendered with a "planned" chip
  plus?: boolean; // section divider, not a feature
}

type Kind = "free" | "paid" | "contract";

interface Tier {
  name: string;
  price: string; // the "price shape", not an invoice
  priceNote?: string;
  module: string; // which module(s) this tier is
  who: string; // one-line "for"
  features: Feature[];
  kind: Kind;
  note: string; // free-vs-paid / add-on line
  highlight?: boolean;
}

// Badge colour + label per kind (matches the theme's four-colour taxonomy).
const KIND_META: Record<Kind, { label: string; color: string }> = {
  free: { label: "free", color: C.green },
  paid: { label: "paid", color: C.steel },
  contract: { label: "contract", color: C.amber },
};

const TIERS: Tier[] = [
  {
    name: "Community",
    price: "$0",
    priceNote: "open source · local & public",
    module: "Private Lab + Control Plane",
    who: "local/public research · single-user security workflows",
    features: [
      { text: "local runner + BYOK (your keys, we never resell tokens)" },
      { text: "public Lab experiments + publish" },
      { text: "local private projects" },
      { text: "EvidenceCase capture" },
      { text: "replay + counterfactuals" },
      { text: "regression corpus (local, unlimited)" },
      { text: "statistics + reproduction bundle + verification" },
      { text: "PDF / HTML artifacts" },
      { text: "Config Builder" },
      { text: "runtime enforcement — gates, degradation, denials" },
      { text: "1 governed node" },
    ],
    kind: "free",
    note: "free forever — safety never checks a license, at any scale",
  },
  {
    name: "Team Workspace",
    price: "$299 / mo",
    priceNote: "card · hosted or self-hosted · 10 governed nodes included",
    module: "Private Lab + Control Plane",
    who: "small teams · hosted private collaboration · production enforcement",
    features: [
      { text: "everything in Community, plus:", plus: true },
      { text: "Control Plane runtime enforcement (pause / stop / intervene)" },
      { text: "production connections (axor-core adapter)" },
      { text: "runtime intervention (inject / excise / replan / budget cap)" },
      { text: "production attestations" },
      { text: "10 governed nodes included" },
      { text: "scheduled regression CI + run history" },
      { text: "hosted private workspace", roadmap: true },
      { text: "multiple members", roadmap: true },
      { text: "shared scenarios", roadmap: true },
      { text: "private artifact links", roadmap: true },
      { text: "limited retention", roadmap: true },
      { text: "includes 10,000 hosted trials", roadmap: true },
      { text: "+$75 / governed node / mo beyond 10", roadmap: true },
    ],
    kind: "paid",
    note: "paid — the self-serve first step, workspace and runtime together",
  },
  {
    name: "Security Workspace",
    price: "$1,500 / mo",
    priceNote: "card · the security unit · 50 governed nodes included",
    module: "Private Lab + Control Plane",
    who: "security teams · incident-to-regression workflow at fleet scale",
    features: [
      { text: "everything in Team, plus:", plus: true },
      { text: "50 governed nodes included" },
      { text: "incident intake + EvidenceCase collaboration", roadmap: true },
      { text: "incident → regression conversion" },
      { text: "scheduled regression suites" },
      { text: "policy / kernel comparison" },
      { text: "approvals + audit trail", roadmap: true },
      { text: "compliance / report exports", roadmap: true },
      { text: "longer history + integration hooks", roadmap: true },
      { text: "includes 50,000 hosted trials", roadmap: true },
      { text: "+$50 / governed node / mo beyond 50", roadmap: true },
    ],
    kind: "paid",
    note: "paid — the primary commercial unit",
    highlight: true,
  },
  {
    name: "Enterprise Platform",
    price: "from $30k / yr",
    priceNote: "contracted · the top rung · negotiated fleet size",
    module: "Private Lab + Control Plane",
    who: "org-wide contract · self-hosted / VPC / air-gapped",
    features: [
      { text: "everything in Security, plus:", plus: true },
      { text: "SSO / SAML / SCIM", roadmap: true },
      { text: "RBAC", roadmap: true },
      { text: "self-hosted backend (Postgres / SQLite)" },
      { text: "VPC / air-gapped deployment", roadmap: true },
      { text: "negotiated governed-node band" },
      { text: "audit + retention policies · compliance exports", roadmap: true },
      { text: "private benchmark registry", roadmap: true },
      { text: "SLA + support", roadmap: true },
    ],
    kind: "contract",
    note: "contracted — the top rung of the same ladder, not a separate product",
  },
];

// Checkout capture (launch-readiness §5): until a real checkout exists, the CTA
// is a mailto that opens a pre-filled order email; VITE_CHECKOUT_URL swaps in a
// Stripe payment link at build time without a code change.
const CHECKOUT_URL: string | undefined =
  (import.meta as unknown as { env?: Record<string, string> }).env?.VITE_CHECKOUT_URL;

function ctaHref(tier: Tier): string {
  if (tier.kind === "free")
    return "https://github.com/Bucha11/axor-control-plane#run-it-docker-compose";
  if (CHECKOUT_URL && tier.kind === "paid") return CHECKOUT_URL;
  const subject = encodeURIComponent(`Axor ${tier.name} — get started`);
  const body = encodeURIComponent(
    "Org:\nModule (Private Lab / Control Plane / both):\nHosted or self-hosted:\nWhat you want to run:\nAnything else:",
  );
  return `mailto:sales@axor.dev?subject=${subject}&body=${body}`;
}

function ctaLabel(tier: Tier): string {
  if (tier.kind === "free") return "Run it now — compose up";
  if (tier.kind === "contract") return "Talk to us";
  return `Get ${tier.name}`;
}

export default function Pricing() {
  return (
    <div style={{ maxWidth: 1040, margin: "0 auto" }}>
      <Coach id="pricing" title="Pricing — one ladder">
        Private Lab and Control Plane are one product on one ladder: a rung gives
        you the evidence workspace and production enforcement together. They used
        to be a rung plus a separately-licensed add-on, which asked every buyer a
        question they could not answer before trying the thing. Features marked{" "}
        <span style={{ color: C.text }}>planned</span> don't exist yet — the page
        refuses to over-claim.
      </Coach>
      <h1 style={{ fontSize: 26, fontWeight: 700, lineHeight: 1.25, margin: "0 0 8px" }}>
        Pricing
      </h1>
      <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.dim, marginBottom: 20, lineHeight: 1.6 }}>
        Line 1 — anything that makes an agent safer is free forever.
        <br />
        Line 2 — you pay for capabilities, organizational maturity and fleet size,
        never for how much you run.
      </div>

      <div
        style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, marginBottom: 24, lineHeight: 1.7 }}
      >
        Community&nbsp;→&nbsp;Team Workspace&nbsp;→&nbsp;Security Workspace&nbsp;→&nbsp;Enterprise Platform
        <br />
        Every rung: the workspace and the Control Plane together. What grows with
        the rung is the governed fleet, not the feature matrix.
      </div>

      <div className="flex gap-4" style={{ flexWrap: "wrap" }}>
        {TIERS.map((t) => {
          const meta = KIND_META[t.kind];
          return (
            <div
              key={t.name}
              className="flex flex-col p-4"
              style={{
                flex: "1 1 190px",
                minWidth: 190,
                background: C.panel,
                border: `1px solid ${t.highlight ? C.steel : C.line}`,
                borderRadius: 10,
              }}
            >
              <div className="flex items-center justify-between mb-1">
                <div style={{ fontFamily: MONO, fontSize: 13.5, fontWeight: 700, color: C.text }}>
                  {t.name}
                </div>
                <span
                  style={{
                    fontFamily: MONO,
                    fontSize: 9,
                    color: meta.color,
                    border: `1px solid ${meta.color}`,
                    borderRadius: 20,
                    padding: "2px 7px",
                    whiteSpace: "nowrap",
                  }}
                >
                  {meta.label}
                </span>
              </div>
              <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, marginBottom: 8 }}>
                {t.module}
              </div>

              <div style={{ fontFamily: MONO, fontSize: 15, color: C.text }}>{t.price}</div>
              {t.priceNote && (
                <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, marginTop: 2, lineHeight: 1.4 }}>
                  {t.priceNote}
                </div>
              )}

              <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, margin: "12px 0", lineHeight: 1.5 }}>
                for: {t.who}
              </div>

              <div style={{ borderTop: `1px solid ${C.line}`, paddingTop: 12 }}>
                {t.features.map((f, i) => (
                  <div
                    key={i}
                    className="flex gap-2 mb-2 items-baseline"
                    style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, lineHeight: 1.4 }}
                  >
                    {!f.plus && <span style={{ color: C.dim }}>·</span>}
                    <span style={{ color: f.plus ? C.dim : C.mut, flex: 1 }}>{f.text}</span>
                    {f.roadmap && (
                      <span
                        style={{
                          fontFamily: MONO,
                          fontSize: 8,
                          color: C.dim,
                          border: `1px solid ${C.line}`,
                          borderRadius: 20,
                          padding: "1px 5px",
                          whiteSpace: "nowrap",
                        }}
                      >
                        planned
                      </span>
                    )}
                  </div>
                ))}
              </div>

              <div
                className="mt-3"
                style={{
                  fontFamily: MONO,
                  fontSize: 10,
                  color: meta.color,
                  borderTop: `1px solid ${C.line}`,
                  paddingTop: 10,
                  marginTop: "auto",
                }}
              >
                {t.note}
              </div>
              <a
                href={ctaHref(t)}
                target={t.kind === "free" ? "_blank" : undefined}
                rel="noreferrer"
                style={{
                  marginTop: 10,
                  textAlign: "center",
                  textDecoration: "none",
                  border: `1px solid ${t.highlight ? C.steel : C.line}`,
                  borderRadius: 6,
                  padding: "8px 12px",
                  fontFamily: MONO,
                  fontSize: 11.5,
                  fontWeight: 700,
                  color: meta.color,
                }}
              >
                {ctaLabel(t)}
              </a>
            </div>
          );
        })}
      </div>

      {/* The Enterprise floor, itemized — so $30k is a concrete product, not a
          number from the air (axor-packaging.md §5). */}
      <div
        className="mt-6 p-4"
        style={{ background: C.panel2, border: `1px solid ${C.line}`, borderRadius: 10 }}
      >
        <div style={{ fontFamily: MONO, fontSize: 12, fontWeight: 700, color: C.text, marginBottom: 6 }}>
          Axor Security Platform — $30,000 / year
        </div>
        <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, lineHeight: 1.7 }}>
          Security Workspace · up to 10 users · self-hosted / VPC runner · scheduled regression CI ·
          incident-to-regression workflows · approvals + audit history · policy comparison · SSO / RBAC ·
          compliance exports · Control Plane for 10 governed nodes · standard support.
          <br />
          Additional governed nodes: $75 / node / month · premium support: +$10k / year · air-gapped: custom.
        </div>
      </div>

      {/* The pilot — a bounded sales motion, not a cheap year of Team
          (axor-packaging.md §6). */}
      <div
        className="mt-4 p-4"
        style={{ background: C.panel2, border: `1px solid ${C.line}`, borderRadius: 10 }}
      >
        <div style={{ fontFamily: MONO, fontSize: 12, fontWeight: 700, color: C.text, marginBottom: 6 }}>
          Incident-to-Regression Pilot — $7,500 · 4–6 weeks
        </div>
        <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, lineHeight: 1.7 }}>
          Fixed scope: one agent · one incident · one simulator/tool path · one EvidenceCase · one policy ·
          a small regression pack · a final technical report. Not included: universal framework integration,
          full CI, production deployment, custom adapter.
          <br />
          100% of the pilot is credited toward a first annual contract ≥ $25k signed within 60 days.
        </div>
      </div>

      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 20, lineHeight: 1.7 }}>
        Hosted plans meter an included <span style={{ color: C.mut }}>hosted-trial allowance</span> as
        cost-control, never the headline — EvidenceCases are included, and inference is BYOK (we don't resell
        tokens). Self-hosted is an annual license with unlimited local execution within the purchased tier —
        no phone-home, offline Ed25519 license. Control Plane is a per-node add-on on a workspace, never a
        second Team/Enterprise tier.
        <br />
        Items marked{" "}
        <span style={{ border: `1px solid ${C.line}`, borderRadius: 20, padding: "1px 6px", fontSize: 8.5 }}>
          planned
        </span>{" "}
        are on the roadmap, not yet shipped — everything else runs today. Canonical packaging, tiers, and
        prices are defined in axor-packaging.md; if this page conflicts with it, that document wins.
      </div>
    </div>
  );
}
