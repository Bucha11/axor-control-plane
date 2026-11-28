// What a workspace would pay a month on each rung, from how many developers
// run coding agents and how many production agents it governs — and which rung
// is the cheapest. The same list prices as the backend's rate card
// (ee/pricing.py: RATE_CARD, SEAT_PRICE, TIER_NODE_CEILING, TIER_DEVELOPER_SEATS):
// change one, change the other (tests/test_billing_tab.py and e2e/pricing.spec.ts
// check the same cases).

export type Rung = "community" | "coding" | "business" | "team" | "security";

interface Card {
  base: number;          // dollars a month
  nodes: number;         // governed nodes included
  perNode: number;       // dollars per node past them
  seats: number;         // developers included
  perSeat: number;       // dollars per developer past them
}

export const CARD: Record<Rung, Card> = {
  community: { base: 0, nodes: 1, perNode: 0, seats: 1, perSeat: 0 },
  coding: { base: 0, nodes: 1, perNode: 75, seats: 0, perSeat: 25 },
  business: { base: 0, nodes: 1, perNode: 75, seats: 0, perSeat: 40 },
  team: { base: 299, nodes: 10, perNode: 75, seats: 0, perSeat: 40 },
  security: { base: 1500, nodes: 50, perNode: 50, seats: 0, perSeat: 40 },
};

export const RUNG_NAME: Record<Rung, string> = {
  community: "Community", coding: "Coding", business: "Business", team: "Team Workspace",
  security: "Security Workspace",
};

// What a rung adds over the one below it, in what ships today — the reason to
// pay more than the cheapest when it is not the cheapest.
export const ADDS: Partial<Record<Rung, string>> = {
  business: "single sign-on, the whole audit trail and its export, who may, SCIM",
  team: "the production side: runtime control, attestations, regression CI on a schedule with its history, alert routing by channel",
  security: "incident → regression conversion, policy / kernel comparison",
};

export interface Quote {
  rung: Rung;
  ok: boolean;           // the rung takes this workspace at all
  why?: string;          // why not
  total: number;
  lines: { text: string; amount: number }[];
}

export function quote(rung: Rung, developers: number, nodes: number): Quote {
  const c = CARD[rung];
  const d = Math.max(0, Math.floor(developers));
  const n = Math.max(0, Math.floor(nodes));
  if (rung === "community" && (d > c.seats || n > c.nodes)) {
    return { rung, ok: false, total: 0, lines: [],
      why: d > c.seats ? "the free plan is one developer" : "the free plan governs one production agent" };
  }
  const extraNodes = Math.max(0, n - c.nodes);
  const extraSeats = Math.max(0, d - c.seats);
  const lines: { text: string; amount: number }[] = [];
  if (c.base) lines.push({ text: `${RUNG_NAME[rung]} — ${c.nodes} production agents included`, amount: c.base });
  if (extraSeats && c.perSeat) {
    lines.push({ text: `${extraSeats} developer${extraSeats === 1 ? "" : "s"} × $${c.perSeat}`, amount: extraSeats * c.perSeat });
  }
  if (extraNodes && c.perNode) {
    lines.push({ text: `${extraNodes} production agent${extraNodes === 1 ? "" : "s"} past the ${c.nodes} included × $${c.perNode}`,
                 amount: extraNodes * c.perNode });
  }
  return { rung, ok: true, total: lines.reduce((k, l) => k + l.amount, 0), lines };
}

/** Every rung's quote, and the cheapest that takes the workspace (the lower
 *  rung on a tie). */
export function quotes(developers: number, nodes: number): { all: Quote[]; best: Quote } {
  const all = (Object.keys(CARD) as Rung[]).map((r) => quote(r, developers, nodes));
  const best = all.filter((q) => q.ok).reduce((a, b) => (b.total < a.total ? b : a));
  return { all, best };
}

// How long the hosted plane keeps runs on each rung (backend
// ee/entitlements.RETENTION_DAYS).
export const RETENTION_DAYS: Record<Rung, number> = {
  community: 7, coding: 30, business: 30, team: 90, security: 365,
};
const LADDER: Rung[] = ["community", "coding", "business", "team", "security"];

function days(n: number): string {
  return n === 365 ? "a year" : `${n} days`;
}

/** What a move down the ladder takes away, said before it is confirmed
 *  (QA-304): the confirm said only "prorated, nothing is bought twice". */
export function lostOnMove(from: Rung, to: Rung): string[] {
  if (LADDER.indexOf(to) >= LADDER.indexOf(from)) return [];
  const lost: string[] = [];
  for (const r of LADDER.slice(LADDER.indexOf(to) + 1, LADDER.indexOf(from) + 1).reverse()) {
    if (r === "team") lost.push("the production side locks (runtime control, attestations, scheduled regression CI, alert routing)");
    if (r === "business") lost.push("single sign-on, SCIM, who may and the full audit trail lock");
    if (r === "security") lost.push("incident → regression conversion and policy / kernel comparison lock");
  }
  if (RETENTION_DAYS[to] < RETENTION_DAYS[from]) {
    lost.push(`runs are kept ${days(RETENTION_DAYS[to])} instead of ${days(RETENTION_DAYS[from])} — older ones are deleted`);
  }
  return lost;
}
