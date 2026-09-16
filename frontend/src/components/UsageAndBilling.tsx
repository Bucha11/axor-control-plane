// Governed-node usage, and the statement drawn from it (monetization §4).
//
// Both were measured, priced, tested — and reachable only by curl, which meant
// a customer could not see their own fleet history or their own bill. A number
// somebody will be asked to pay has to be visible to them before the invoice
// arrives, not after.
//
// Reported, never enforced: being over the ceiling is a conversation with the
// vendor, and it never turns governance off, because safety never checks a
// license.
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { C, MONO } from "../theme";
import Tooltip from "./Tooltip";

function money(cents: number, currency: string): string {
  // Integer cents all the way from ee/pricing.py; formatted once, here.
  return `${(cents / 100).toFixed(2)} ${currency}`;
}

export default function UsageAndBilling() {
  const [month, setMonth] = useState<string | undefined>(undefined);
  const usage = useQuery({ queryKey: ["license-usage"], queryFn: () => api.licenseUsage(6) });
  const invoice = useQuery({
    queryKey: ["license-invoice", month],
    queryFn: () => api.licenseInvoice(month),
    // 404 is the honest answer for an unlicensed deployment: usage is still
    // measured, there is just nothing to bill. Not worth retrying.
    retry: false,
  });

  const ceiling = usage.data?.governed_node_ceiling ?? null;
  // An unlicensed deployment is a STATE, not a failure: usage is still measured,
  // there is simply nothing to bill. Rendering the raw 404 envelope would put a
  // status code in front of a customer for a situation that is entirely normal.
  const unlicensed =
    invoice.isError && /\b404\b/.test((invoice.error as Error).message);

  return (
    <div data-testid="usage-billing">
      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, marginBottom: 10, lineHeight: 1.6 }}>
        A month is billed on its PEAK — the most governed nodes on any one day,
        and you can point at the day. Distinct counts every node that ever
        reported, which is higher whenever nodes are replaced rather than added;
        it is shown for context and is never the basis.
      </div>

      {usage.isError && (
        <div style={{ fontFamily: MONO, fontSize: 11, color: C.red }}>
          {(usage.error as Error).message}
        </div>
      )}

      {(usage.data?.months ?? []).map((m) => (
        <div
          key={m.month}
          className="flex items-center gap-3 py-1"
          style={{ fontFamily: MONO, fontSize: 11, cursor: "pointer" }}
          onClick={() => setMonth(m.month)}
        >
          <span style={{ color: month === m.month ? C.steel : C.text, width: 62 }}>{m.month}</span>
          <Tooltip content="The most governed nodes that reported on any single day this month — the billing basis.">
            <span style={{ color: m.over_ceiling ? C.amber : C.text }}>
              peak {m.peak_nodes}
              {m.peak_day ? <span style={{ color: C.dim }}> on {m.peak_day}</span> : null}
            </span>
          </Tooltip>
          <span style={{ color: C.dim, fontSize: 10 }}>distinct {m.distinct_nodes}</span>
          {ceiling !== null && (
            <span style={{ color: C.dim, fontSize: 10 }}>of {ceiling} included</span>
          )}
          {m.over_ceiling && (
            <span style={{ color: C.amber, fontSize: 10 }}>over ceiling</span>
          )}
        </div>
      ))}
      {usage.data && usage.data.months.every((m) => m.peak_nodes === 0) && (
        <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>
          no governed node has reported yet — nothing metered.
        </div>
      )}

      <div className="mt-3 pt-2" style={{ borderTop: `1px solid ${C.line}` }}>
        <div style={{ fontSize: 9.5, fontFamily: MONO, color: C.dim, letterSpacing: "0.08em", marginBottom: 5 }}>
          STATEMENT {month ? `· ${month}` : "· last closed month"}
        </div>
        {unlicensed ? (
          <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>
            no license on this deployment, so there is nothing to bill — the
            usage above is measured either way.
          </div>
        ) : invoice.isError ? (
          <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.red }}>
            {(invoice.error as Error).message}
          </div>
        ) : invoice.data ? (
          <div style={{ fontFamily: MONO, fontSize: 11 }}>
            <div style={{ color: C.text }}>
              {invoice.data.organization} · {invoice.data.workspace_tier}
              {invoice.data.provisional && (
                <Tooltip content="The month is still running, so the peak — and the total — can still rise.">
                  <span style={{ color: C.amber, fontSize: 10 }}> · provisional</span>
                </Tooltip>
              )}
            </div>
            {invoice.data.priced ? (
              <>
                <div style={{ color: C.mut, fontSize: 10.5, marginTop: 3 }}>
                  base {money(invoice.data.base_cents, invoice.data.currency)}
                  {" · "}
                  {invoice.data.billable_nodes > 0
                    ? `${invoice.data.billable_nodes} node(s) past the ${invoice.data.included_nodes} included → ${money(invoice.data.overage_cents, invoice.data.currency)}`
                    : `peak ${invoice.data.peak_nodes} within the ${invoice.data.included_nodes} included`}
                </div>
                <div style={{ color: C.text, marginTop: 3 }}>
                  total {money(invoice.data.total_cents, invoice.data.currency)}
                </div>
              </>
            ) : (
              // A contracted rung has no list price; inventing one would put a
              // figure nobody agreed to in front of a customer.
              <div style={{ color: C.mut, fontSize: 10.5, marginTop: 3 }}>
                {invoice.data.note}
              </div>
            )}
            {invoice.data.priced && (
              <div style={{ color: C.dim, fontSize: 10, marginTop: 3 }}>{invoice.data.note}</div>
            )}
          </div>
        ) : (
          <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>loading…</div>
        )}
      </div>
    </div>
  );
}
