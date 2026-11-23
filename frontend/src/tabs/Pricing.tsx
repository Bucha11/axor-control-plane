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
import { LAB_SHIPPED } from "../flags";
import { navigate, useRoute } from "../router";
import { ADDS, quotes, RUNG_NAME, type Rung } from "../quote";

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
    blurb: "Your developers' coding agents and your production agents: say how many, and see what each plan costs." },
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
  // what this plan says on one way in, where its general card would talk past
  // the reader (Community's Lab lines mean nothing to someone with Claude Code)
  on?: Partial<Record<Journey, Partial<Pick<Tier, "module" | "who" | "features" | "note">>
    & { cta?: { label: string; route: string } }>>;
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
    on: {
      coding: {
        module: "Coding agents",
        who: "one developer, one agent family",
        features: [
          { text: "Claude Code or Codex — one agent family" },
          { text: "every tool call checked before it runs; the dangerous ones blocked" },
          { text: "text from the web stays marked, whichever session carries it" },
          { text: "agent trees of that family — a planner, read-only readers, an implementer" },
          { text: "findings checked against the files their agent opened" },
          { text: "every session on the Control Plane: pause, stop, cap" },
          { text: "pair on a session with your team — watch it live, send the agent a note" },
          { text: "your own policy: which of the rules your agents are refused" },
          { text: "any number of repositories and sessions" },
        ],
        note: "free forever — what gets blocked never depends on the plan",
        cta: { label: "Connect your coding agents", route: "connect?for=coding" },
      },
    },
  },
  {
    name: "Coding",
    price: "$25 / developer / mo",
    priceNote: "card · per developer whose coding agents ran that month",
    module: "Coding agents + Control Plane",
    who: "teams governing Claude Code and Codex · and a production agent or two",
    features: [
      { text: "everything free, plus what you pay for:", plus: true },
      { text: "Claude Code and Codex together — one writes, the other reviews, in one tree" },
      { text: "the whole team in one workspace — every developer's sessions in one place" },
      { text: "team memory — what your agents verifiably learned in a repo, given to every agent at session start" },
      { text: "one policy for the whole team · stop anyone's session, cap its budget" },
      { text: "pair with people from outside the team — up to 3 guests per session, on their own free accounts" },
      { text: "reputation per agent and model, across the team's sessions" },
      { text: "billed per developer whose agents ran that month — not per session, repo or tree" },
      { text: "a production agent or two as well: one included, then $75 per agent a month — pause, stop and cap them from the plane" },
      { text: "the audit trail of the last 30 days — who did what to which agent" },
    ],
    kind: "paid",
    note: "paid — coding agents, and a few production agents; with many of those, Team is cheaper",
    journeys: ["coding", "both"],
    highlightFor: ["coding"],
  },
  {
    name: "Business",
    price: "$40 / developer / mo",
    priceNote: "card · per developer whose coding agents ran that month",
    module: "Coding agents + Control Plane, for a company",
    who: "companies: their people sign in through their own identity provider",
    features: [
      { text: "everything in Coding, plus:", plus: true },
      { text: "single sign-on — OpenID Connect (Okta, Entra ID, Google Workspace), the only way in if you say so" },
      { text: "the whole audit trail — every control, who and when — exported as CSV or JSON for your archive" },
      { text: "who may: let a lead stop a teammate's session, pull the team's brake or grant exceptions — per person" },
      { text: "SCIM — people come and go with your identity provider" },
      { text: "SAML", roadmap: true },
    ],
    kind: "paid",
    note: "paid — Team and Security include it",
    journeys: ["coding", "both"],
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
      { text: "+$40 / developer / mo beyond 5 (a Business seat)" },
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
      { text: "+$40 / developer / mo beyond 25 (a Business seat)" },
    ],
    kind: "paid",
    note: "paid — the primary commercial unit",
    journeys: ["production", "both"],
  },
  {
    name: "Enterprise Platform",
    price: "from $30k / yr",
    priceNote: "contracted · the top rung · negotiated fleet size",
    module: "Private Lab + Control Plane",
    who: "org-wide contract · self-hosted / VPC / air-gapped",
    features: [
      { text: "everything in Security, plus:", plus: true },
      { text: "single sign-on — OpenID Connect (Okta, Entra ID, Google Workspace), enforceable per domain" },
      { text: "SAML · SCIM provisioning", roadmap: true },
      { text: "who may: stop a teammate's session, pull the brake, grant exceptions — per person" },
      { text: "self-hosted backend (Postgres / SQLite)" },
      { text: "VPC / air-gapped deployment", roadmap: true },
      { text: "negotiated governed-node band" },
      { text: "negotiated developer seats" },
      { text: "audit trail of every control — who did what to which agent — CSV / JSON export" },
      { text: "retention policies · compliance reports", roadmap: true },
      { text: "private benchmark registry", roadmap: true },
      { text: "SLA + support", roadmap: true },
    ],
    kind: "contract",
    note: "contracted — the top rung of the same ladder, not a separate product",
    journeys: ["coding", "production", "both"],
    on: {
      coding: {
        module: "Coding agents, org-wide",
        who: "the whole engineering org · SSO · self-hosted",
        features: [
          { text: "everything in Coding, plus:", plus: true },
          { text: "single sign-on — OpenID Connect (Okta, Entra ID, Google Workspace), enforceable per domain" },
          { text: "SAML · SCIM provisioning", roadmap: true },
          { text: "who may: stop a teammate's session, pull the brake, grant exceptions — per person" },
          { text: "self-hosted backend (Postgres / SQLite)" },
          { text: "VPC / air-gapped deployment", roadmap: true },
          { text: "negotiated developer seats" },
          { text: "audit trail of every control — who did what to which agent — CSV / JSON export" },
          { text: "retention policies · compliance reports", roadmap: true },
          { text: "SLA + support", roadmap: true },
        ],
      },
    },
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

// Lab is not shipping yet (flags.LAB_SHIPPED): its lines come off the ladder
// and the module reads as the Control Plane alone.
const shownTier = (t: Tier): Tier => LAB_SHIPPED ? t : {
  ...t,
  module: t.module.replace("Private Lab + ", ""),
  features: t.features.filter((f) => !/\bLab\b/.test(f.text)),
};

export default function Pricing() {
  const route = useRoute();
  const [journey, setJourney] = useState<Journey>(() => initialJourney(route.query));
  const shown = TIERS.filter((t) => t.journeys.includes(journey)).map(shownTier);
  // the calculator's two numbers, and the cheapest rung for them
  const [devs, setDevs] = useState(5);
  const [agents, setAgents] = useState(1);
  const calc = quotes(devs, agents);
  return (
    <div style={{ maxWidth: 1040, margin: "0 auto" }}>
      <Coach id="pricing" title="Pricing — one ladder">
        {LAB_SHIPPED
          ? <>Private Lab and Control Plane are one product on one ladder: a rung gives
            you the evidence workspace and production enforcement together. </>
          : <>Each rung includes everything below it — no separately-licensed add-ons.
            What gets blocked never depends on the rung. </>}
        Features marked{" "}
        <span style={{ color: C.text }}>planned</span> don't exist yet — the page
        refuses to over-claim.
      </Coach>
      <h1 style={{ fontSize: 26, fontWeight: 700, lineHeight: 1.25, margin: "0 0 8px" }}>
        Pricing
      </h1>
      <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.dim, marginBottom: 20, lineHeight: 1.6 }}>
        Protection is free on every plan — a license never decides what gets blocked.
        <br />
        {journey === "coding"
          ? "You pay for a team, and for mixing agents."
          : "You pay for capabilities, organizational maturity and fleet size, never for how much you run."}
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
          ? "Free: one developer and one agent family. A developer is counted once a month, however many sessions, agents or trees they ran."
          : journey === "production"
            ? "Community → Team Workspace → Security Workspace → Enterprise Platform. What grows with the rung is the governed fleet, not the feature matrix."
            : null}
      </div>
      {journey === "both" && (
        <Calculator devs={devs} agents={agents} setDevs={setDevs} setAgents={setAgents} calc={calc} />
      )}

      <div className="flex gap-4" style={{ flexWrap: "wrap" }}>
        {shown.map((base) => {
          const { cta, ...over } = base.on?.[journey] ?? {};
          const t: Tier = { ...base, ...over };
          // on Both, the cheapest plan for the numbers above
          const highlight = journey === "both"
            ? RUNG_NAME[calc.best.rung] === t.name
            : t.highlightFor?.includes(journey) ?? false;
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
                href={cta ? `#/${cta.route}` : ctaHref(t)}
                onClick={cta ? (e) => { e.preventDefault(); navigate(cta.route); } : undefined}
                data-testid={`cta-${t.name.split(" ")[0].toLowerCase()}`}
                target={t.kind === "free" && !cta ? "_blank" : undefined}
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
                {cta ? cta.label : ctaLabel(t)}
              </a>
            </div>
          );
        })}
      </div>

      {/* The Enterprise floor, itemized — so $30k is a concrete product, not a
          number from the air (axor-packaging.md §5). Production-side: a coding
          team has nothing to read in it. */}
      {journey !== "coding" && (<>
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
      </>)}

      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 20, lineHeight: 1.7 }}>
        {journey !== "coding" && (<>
        Hosted plans meter an included <span style={{ color: C.mut }}>hosted-trial allowance</span> as
        cost-control, never the headline — EvidenceCases are included, and inference is BYOK (we don't resell
        tokens). Self-hosted is an annual license with unlimited local execution within the purchased tier —
        no phone-home, offline Ed25519 license. Control Plane is not a separate purchase: a rung carries the
        evidence workspace and production enforcement together, with an included governed-node count and a
        per-node rate above it.
        <br />
        </>)}
        Items marked{" "}
        <span style={{ border: `1px solid ${C.line}`, borderRadius: 20, padding: "1px 6px", fontSize: 8.5 }}>
          planned
        </span>{" "}
        are on the roadmap, not yet shipped — everything else runs today.
      </div>
    </div>
  );
}


/** Both ways in: how many developers run coding agents, how many production
 *  agents — what each plan costs a month, and the cheapest. List prices; the
 *  bill itself is drawn from what actually ran (Settings → Billing). */
function Calculator({ devs, agents, setDevs, setAgents, calc }: {
  devs: number; agents: number; setDevs: (n: number) => void; setAgents: (n: number) => void;
  calc: ReturnType<typeof quotes>;
}) {
  const field = { background: C.bg, border: `1px solid ${C.line}`, borderRadius: 5, color: C.text,
                  fontFamily: MONO, fontSize: 13, padding: "4px 8px", width: 80 };
  const num = (v: string) => Math.max(0, Math.min(10_000, Math.floor(Number(v) || 0)));
  const best = calc.best;
  // the next rung up that costs more but adds something — the reason to pay it
  const next = calc.all.find((q) => q.ok && q.total > best.total && ADDS[q.rung as Rung]);
  return (
    <div data-testid="pricing-calculator" className="p-4" style={{ background: C.panel, border: `1px solid ${C.steel}`,
      borderRadius: 10, marginBottom: 24, fontFamily: MONO, fontSize: 12, color: C.mut, lineHeight: 1.7 }}>
      <div className="flex gap-6 flex-wrap items-end">
        <label className="flex flex-col gap-1">
          <span style={{ fontSize: 10.5, color: C.dim }}>developers who run coding agents</span>
          <input type="number" min={0} value={devs} aria-label="developers" data-testid="calc-devs"
            onChange={(e) => setDevs(num(e.target.value))} style={field} />
        </label>
        <label className="flex flex-col gap-1">
          <span style={{ fontSize: 10.5, color: C.dim }}>production agents</span>
          <input type="number" min={0} value={agents} aria-label="production agents" data-testid="calc-agents"
            onChange={(e) => setAgents(num(e.target.value))} style={field} />
        </label>
        <div style={{ flex: 1, minWidth: 220 }} data-testid="calc-best">
          <div style={{ fontSize: 10.5, color: C.dim }}>cheapest</div>
          <span style={{ color: C.text, fontSize: 15 }}>{RUNG_NAME[best.rung]} — ${best.total.toLocaleString("en-US")} / month</span>
        </div>
      </div>
      <div className="mt-3" data-testid="calc-lines" style={{ fontSize: 11 }}>
        {best.lines.length === 0 ? (
          <div>free — one developer with one agent family (Claude Code or Codex), one production agent</div>
        ) : best.lines.map((l, i) => (
          <div key={i} className="flex gap-3">
            <span style={{ flex: 1 }}>{l.text}</span>
            <span style={{ color: C.text }}>${l.amount.toLocaleString("en-US")}</span>
          </div>
        ))}
      </div>
      {next && (
        <div className="mt-2" data-testid="calc-next" style={{ fontSize: 11, color: C.dim }}>
          {RUNG_NAME[next.rung]} is ${(next.total - best.total).toLocaleString("en-US")} more
          (${next.total.toLocaleString("en-US")}) and adds {ADDS[next.rung as Rung]}.
        </div>
      )}
      <div className="mt-3 flex gap-4 flex-wrap" data-testid="calc-all" style={{ fontSize: 10.5 }}>
        {calc.all.map((q) => (
          <span key={q.rung} style={{ color: q.rung === best.rung ? C.text : C.dim }}>
            {RUNG_NAME[q.rung]}: {q.ok ? `$${q.total.toLocaleString("en-US")}` : `— ${q.why}`}
          </span>
        ))}
        <span style={{ color: C.dim }}>Enterprise: by contract</span>
      </div>
      <div className="mt-2" style={{ fontSize: 10, color: C.dim }}>
        list prices a month. A developer counts once a month when their agents run; a production agent is
        billed on the month&apos;s peak. What gets blocked never depends on the plan.
      </div>
    </div>
  );
}
