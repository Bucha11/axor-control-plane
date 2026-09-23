// The org's plan — one plan for the Control Plane and the Lab, bought and
// managed through axor-identity (see billing.ts). Shown only where billing is
// on (the hosted service); a self-hosted deployment licenses below instead.
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import {
  BUYABLE_TIERS,
  TIER_LABEL,
  TIER_PRICE,
  billingError,
  openPortal,
  pendingPlan,
  startCheckout,
} from "../billing";
import { useRoute } from "../router";
import { useApp } from "../store";
import { C, MONO, btn } from "../theme";

const OUTCOME: Record<string, { text: string; color: string }> = {
  success: { text: "Payment received — your plan is active.", color: C.green },
  canceled: { text: "Checkout closed. Nothing was charged.", color: C.dim },
  unavailable: { text: "Checkout is unavailable right now. Try again shortly.", color: C.amber },
};

export default function BillingPanel() {
  const signedIn = Boolean(useApp((s) => s.identityEmail));
  const route = useRoute();
  const qc = useQueryClient();
  const config = useQuery({ queryKey: ["billing-config"], queryFn: api.billingConfig, retry: false });
  const status = useQuery({
    queryKey: ["billing-status"],
    queryFn: api.billingStatus,
    enabled: signedIn && Boolean(config.data?.enabled),
  });
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const autoStarted = useRef(false);

  async function run(label: string, action: () => Promise<void>) {
    setBusy(label);
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(billingError(err));
      setBusy(null);
      void qc.invalidateQueries({ queryKey: ["billing-status"] });
    }
  }

  // A plan picked before signing in starts its checkout once the org is known
  // to be on the free plan.
  const pending = pendingPlan();
  const tier = status.data?.tier;
  const canBuy =
    status.data?.is_admin && !status.data.subscription && status.data.tier === "community";
  useEffect(() => {
    if (!autoStarted.current && pending && canBuy) {
      autoStarted.current = true;
      void run(pending, () => startCheckout(pending));
    }
  }, [pending, canBuy]);

  if (!config.data?.enabled) return null;

  const outcome = OUTCOME[route.query.billing ?? ""];
  const buyable = (config.data.tiers ?? []).filter((t) =>
    (BUYABLE_TIERS as readonly string[]).includes(t),
  );
  const sub = status.data?.subscription;

  return (
    <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, display: "flex", flexDirection: "column", gap: 8 }}>
      {outcome && <div style={{ color: outcome.color }}>{outcome.text}</div>}
      {!signedIn ? (
        <div>
          Sign in above to see or change your plan.
          {pending && (
            <span style={{ color: C.text }}> You picked {TIER_LABEL[pending] ?? pending}; checkout opens after sign-in.</span>
          )}
        </div>
      ) : status.isPending ? (
        <div style={{ color: C.dim }}>loading plan…</div>
      ) : status.isError ? (
        <div style={{ color: C.red }}>cannot read your plan: {billingError(status.error)}</div>
      ) : (
        <>
          <div>
            plan <span style={{ color: C.text }}>{TIER_LABEL[tier ?? ""] ?? tier}</span>
            {sub && (
              <>
                {" "}· {sub.status}
                {sub.current_period_end && (
                  <>
                    {" "}· {sub.scheduled_change === "cancel" ? "ends" : "renews"}{" "}
                    {sub.current_period_end.slice(0, 10)}
                  </>
                )}
              </>
            )}
            <span style={{ color: C.dim }}> · covers the Control Plane and the Lab</span>
          </div>
          <div className="flex items-center gap-2" style={{ flexWrap: "wrap" }}>
            {canBuy &&
              buyable.map((t) => (
                <button
                  key={t}
                  disabled={busy !== null}
                  onClick={() => void run(t, () => startCheckout(t))}
                  style={btn({ color: C.steel, borderColor: C.steel, fontSize: 11, padding: "5px 12px" })}
                >
                  {busy === t ? "opening checkout…" : `upgrade to ${TIER_LABEL[t]} · ${TIER_PRICE[t] ?? ""}`}
                </button>
              ))}
            {status.data?.can_manage && (
              <button
                disabled={busy !== null}
                onClick={() => void run("portal", openPortal)}
                style={btn({ color: C.text, fontSize: 11, padding: "5px 12px" })}
              >
                {busy === "portal" ? "opening…" : "manage billing — change plan, card, cancel"}
              </button>
            )}
            {!canBuy && !status.data?.can_manage && (
              <span style={{ color: C.dim }}>
                {tier === "enterprise"
                  ? "Enterprise is contracted — contact us to change it."
                  : "Ask an owner or admin of your organization to change the plan."}
              </span>
            )}
          </div>
        </>
      )}
      {error && <div style={{ color: C.red }}>{error}</div>}
    </div>
  );
}
