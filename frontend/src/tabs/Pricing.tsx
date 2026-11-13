// Pricing — canonical ladder from axor-packaging.md (SINGLE SOURCE OF TRUTH).
// ONE LADDER. Private Lab (the security / evidence workspace) and Control Plane
// (production enforcement) are one product sold on one ladder: a rung entitles
// both. They used to be a rung plus a separately-licensed add-on, which asked
// every buyer a question they could not answer before trying the thing — and
// the entitlement code carried a module flag that was signed, displayed, and
// decided nothing. Static, no backend. Safety and hobby-scale privacy are free
// forever; you pay for hosted collaboration, the security workflow, and the
// size of the governed fleet.
import { useState } from "react";
import { C, MONO, btn } from "../theme";
import Coach from "../components/Coach";
import Tabs from "../components/Tabs";
import { navigate, useRoute } from "../router";

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

// Three ways in. Someone governing Claude Code / Codex does not care about the
// production runtime, someone wrapping a production agent does not care about
// coding agents, and some want both — each sees the plans for their way in,
// metered the way their agents run: coding agents by DEVELOPER (a session is
// not a governed node), production agents by governed node.
export type Journey = "coding" | "production" | "both";

export const JOURNEYS: { id: Journey; label: string; blurb: string }[] = [
  { id: "coding", label: "Coding agents",
    blurb: "Claude Code and Codex, governed through their hooks — priced per developer." },
  { id: "production", label: "A production agent",
    blurb: "Your own agent in production, wrapped and governed — priced per governed node." },
  { id: "both", label: "Both",
    blurb: "One plan: the production fleet and your developers' coding agents." },
];

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
  journeys: Journey[]; // which ways in show this plan
  highlightFor?: Journey[];
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
      { text: "hosted Lab runs — 1 runtime, 50 runs / month" },
      { text: "EvidenceCase capture" },
      { text: "replay + counterfactuals" },
      { text: "regression corpus (local, unlimited)" },
      { text: "statistics + reproduction bundle + verification" },
      { text: "PDF / HTML artifacts" },
      { text: "Config Builder" },
      { text: "runtime enforcement — gates, degradation, denials" },
      { text: "1 governed node" },
      { text: "1 developer, 1 agent — Claude Code or Codex, with hooks, trees and control" },
    ],
    kind: "free",
    note: "free forever — safety never checks a license, at any scale",
    journeys: ["coding", "production", "both"],
  },
  {
    name: "Coding",
    price: "$25 / developer / mo",
    priceNote: "card · per developer whose coding agents ran that month",
    module: "Coding agents + Control Plane",
    who: "teams governing Claude Code and Codex · no production runtime needed",
    features: [
      { text: "everything in Community, for every developer on the team:", plus: true },
      { text: "Claude Code and Codex together — one writes, the other reviews, in one tree" },
      { text: "where text came from is tracked across every agent in a repository" },
      { text: "agent trees — planner → parallel readers → implementer, any agent / model" },
      { text: "read-only readers, findings checked against the files they opened" },
      { text: "ranked, budgeted context between agents · cut from the Control Plane" },
      { text: "every session on the Control Plane: pause, stop, call budget" },
      { text: "reputation per agent and model, across sessions" },
      { text: "a developer = someone whose agents reported that month, not a session" },
    ],
    kind: "paid",
    note: "paid — coding agents only; add production agents with Team",
    journeys: ["coding"],
    highlightFor: ["coding"],
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
      { text: "+$75 / governed node / mo beyond 10" },
      { text: "5 developer seats included — everything in Coding" },
      { text: "+$25 / developer / mo beyond 5" },
    ],
    kind: "paid",
    note: "paid — the self-serve first step, workspace and runtime together",
    journeys: ["production", "both"],
    highlightFor: ["production"],
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
      { text: "+$50 / governed node / mo beyond 50" },
      { text: "25 developer seats included — everything in Coding" },
      { text: "+$25 / developer / mo beyond 25" },
    ],
    kind: "paid",
    note: "paid — the primary commercial unit",
    journeys: ["production", "both"],
    highlightFor: ["both"],
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
      { text: "negotiated developer seats" },
      { text: "audit + retention policies · compliance exports", roadmap: true },
      { text: "private benchmark registry", roadmap: true },
      { text: "SLA + support", roadmap: true },
    ],
    kind: "contract",
    note: "contracted — the top rung of the same ladder, not a separate product",
    journeys: ["coding", "production", "both"],
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
    "Org:\nGoverned nodes (roughly):\nHosted or self-hosted:\nWhat you want to run:\nAnything else:",
  );
  return `mailto:sales@axor.dev?subject=${subject}&body=${body}`;
}

function ctaLabel(tier: Tier): string {
  if (tier.kind === "free") return "Run it now — compose up";
  if (tier.kind === "contract") return "Talk to us";
  return `Get ${tier.name}`;
}

function initialJourney(query: Record<string, string>): Journey {
  const want = query.for;
  return want === "coding" || want === "production" || want === "both" ? want : "both";
}

export default function Pricing() {
  const route = useRoute();
  const [journey, setJourney] = useState<Journey>(() => initialJourney(route.query));
  const shown = TIERS.filter((t) => t.journeys.includes(journey));
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

      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginBottom: 6, letterSpacing: "0.06em" }}>
        WHAT ARE YOU GOVERNING?
      </div>
      <Tabs size="lg" style={{ marginBottom: 12 }} active={journey}
        onChange={(id) => { setJourney(id); navigate(`pricing?for=${id}`); }}
        items={JOURNEYS.map((j) => ({ id: j.id, label: j.label, testId: `journey-${j.id}` }))} />
      <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginBottom: 24, lineHeight: 1.7 }}>
        {JOURNEYS.find((j) => j.id === journey)!.blurb}
        <br />
        {journey === "coding"
          ? "A developer is counted once a month, however many sessions, agents or trees they ran. One developer is free."
          : journey === "production"
            ? "Community → Team Workspace → Security Workspace → Enterprise Platform. What grows with the rung is the governed fleet, not the feature matrix."
            : "Team and Security include developer seats, so one plan covers the fleet and the coding agents. Only coding agents? The Coding plan is cheaper."}
      </div>

      <div className="flex gap-4" style={{ flexWrap: "wrap" }}>
        {shown.map((t) => {
          const highlight = t.highlightFor?.includes(journey) ?? false;
          const meta = KIND_META[t.kind];
          return (
            <div
              key={t.name}
              className="flex flex-col p-4"
              style={{
                flex: "1 1 190px",
                minWidth: 190,
                background: C.panel,
                border: `1px solid ${highlight ? C.steel : C.line}`,
                borderRadius: 10,
              }}
              data-testid={`tier-${t.name.split(" ")[0].toLowerCase()}`}
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
                  border: `1px solid ${highlight ? C.steel : C.line}`,
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
        no phone-home, offline Ed25519 license. Control Plane is not a separate purchase: a rung carries the
        evidence workspace and production enforcement together, with an included governed-node count and a
        per-node rate above it.
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
