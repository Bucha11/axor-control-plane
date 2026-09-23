// Buying a plan. Billing lives in axor-identity and is shared with the Lab: one
// org, one plan, both products (the Lab runs this same flow). The server never
// sees a card — checkout is Paddle's page, reached through /identity/v1/billing/pay
// on this origin, which sends the payer back here with ?billing=<outcome>.
import { api, forceRefresh } from "./api";

// The plans a checkout can buy. Community is free; Enterprise is contracted.
export const BUYABLE_TIERS = ["team", "security"] as const;
export const TIER_LABEL: Record<string, string> = {
  community: "Community",
  team: "Team Workspace",
  security: "Security Workspace",
  enterprise: "Enterprise",
};
export const TIER_PRICE: Record<string, string> = {
  team: "$299 / month",
  security: "$1,500 / month",
};

// A plan picked before signing in — from the Pricing tab or a landing page's
// ?plan= link — survives the sign-up and starts the checkout right after it.
const PENDING_KEY = "axor.pendingPlan";

export function rememberPlan(tier: string): void {
  if (!(BUYABLE_TIERS as readonly string[]).includes(tier)) return;
  try {
    sessionStorage.setItem(PENDING_KEY, tier);
  } catch {
    /* storage unavailable: the user picks the plan again after signing in */
  }
}

export function pendingPlan(): string | null {
  try {
    return sessionStorage.getItem(PENDING_KEY);
  } catch {
    return null;
  }
}

export function clearPendingPlan(): void {
  try {
    sessionStorage.removeItem(PENDING_KEY);
  } catch {
    /* nothing to clear */
  }
}

/** Open the provider checkout for `tier`; the payer comes back to Settings. */
export async function startCheckout(tier: string): Promise<void> {
  clearPendingPlan();
  const back = `${window.location.origin}${window.location.pathname}#/settings`;
  const { checkout_url } = await api.billingCheckout(tier, back);
  window.location.assign(checkout_url);
}

export async function openPortal(): Promise<void> {
  const { url } = await api.billingPortal();
  window.location.assign(url);
}

/** The server's reason, out of the `"<status> <json>"` an api call throws. */
export function billingError(err: unknown): string {
  const text = err instanceof Error ? err.message : String(err);
  const body = text.replace(/^\d{3}\s*/, "");
  try {
    const detail = JSON.parse(body)?.detail;
    if (typeof detail === "string") return detail;
  } catch {
    /* not JSON */
  }
  return text || "billing request failed";
}

/**
 * Consume `?billing=<outcome>` (back from checkout) and `?plan=<tier>` (from a
 * landing page) once, at load. After a successful payment the webhook can land
 * a moment after the redirect, so the session is refreshed until the new plan
 * shows up (a few seconds at most) — then every request carries it.
 */
export async function consumeBillingParams(): Promise<string | null> {
  const params = new URLSearchParams(window.location.search);
  const outcome = params.get("billing");
  const plan = params.get("plan");
  if (!outcome && !plan) return null;
  if (plan) rememberPlan(plan);
  params.delete("billing");
  params.delete("plan");
  const query = params.toString();
  window.history.replaceState(
    null,
    "",
    `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`,
  );
  if (outcome === "success") {
    for (let attempt = 0; attempt < 6; attempt++) {
      await forceRefresh();
      const status = await api.billingStatus().catch(() => null);
      if (status && status.tier !== "community" && status.token_tier === status.tier) break;
      await new Promise((resolve) => setTimeout(resolve, 2000));
    }
  }
  return outcome ?? "plan";
}
