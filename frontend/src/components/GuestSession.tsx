// A hosted visitor works as a guest before signing up, and keeps the work.
//
// - On load, with auth on and nothing to authenticate with, it starts a guest
//   session (the plane gives the guest an org of its own for 30 minutes).
// - Where a feature needs an account (a share link, notifications, the
//   regression corpus…), the plane answers `account_required` and this asks
//   for a sign-up right there, naming what it unlocks.
// - On sign-in it claims every guest session this tab held into the account
//   and says what moved.
import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, startGuest } from "../api";
import { isGuestToken, useApp } from "../store";
import { C, MONO, btn } from "../theme";
import { IdentityLogin } from "../tabs/Settings";

const LABELS: [string, string][] = [
  ["runs", "run"], ["desired_state", "agent"], ["probe_reports", "health check"],
  ["reputation_snapshots", "reputation snapshot"], ["heal_attempts", "repair"],
];

function describeMoved(moved: Record<string, number>): string {
  const parts = LABELS
    .filter(([table]) => moved[table])
    .map(([table, word]) => `${moved[table]} ${word}${moved[table] === 1 ? "" : "s"}`);
  return parts.length ? parts.join(", ") : "your guest work";
}

export default function GuestSession() {
  const qc = useQueryClient();
  const apiToken = useApp((s) => s.apiToken);
  const refreshToken = useApp((s) => s.refreshToken);
  const guestTokens = useApp((s) => s.guestTokens);
  const [ask, setAsk] = useState<string | null>(null);
  const [moved, setMoved] = useState<string | null>(null);

  // nothing to authenticate with: start as a guest, if the plane offers it
  useEffect(() => {
    if (apiToken) return;
    let alive = true;
    void api.authStatus().then((st) => {
      if (!alive || !st.auth_enabled || !st.guest_sessions || st.authenticated) return;
      void startGuest().then((ok) => {
        if (ok) void qc.invalidateQueries();
      });
    }).catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [apiToken, qc]);

  // signed in with guest work behind: claim all of it
  useEffect(() => {
    if (!apiToken || isGuestToken(apiToken) || !refreshToken || guestTokens.length === 0) return;
    const tokens = [...guestTokens];
    useApp.getState().clearGuests();
    void Promise.all(tokens.map((t) => api.claimGuest(t).catch(() => null))).then((results) => {
      const total: Record<string, number> = {};
      for (const r of results) {
        for (const [k, v] of Object.entries(r?.moved ?? {})) total[k] = (total[k] ?? 0) + v;
      }
      if (Object.keys(total).length) setMoved(describeMoved(total));
      void qc.invalidateQueries();
    });
  }, [apiToken, refreshToken, guestTokens, qc]);

  // a guest reached an account-only feature
  useEffect(() => {
    const on = (e: Event) => {
      const detail = (e as CustomEvent<{ feature?: string }>).detail;
      if (isGuestToken(useApp.getState().apiToken)) setAsk(detail?.feature ?? "this");
    };
    window.addEventListener("axor:account-required", on);
    return () => window.removeEventListener("axor:account-required", on);
  }, []);

  return (
    <>
      {moved && (
        <div data-testid="guest-claimed" style={{
          maxWidth: 1040, margin: "0 auto 12px", fontFamily: MONO, fontSize: 11.5, color: C.green,
          border: `1px solid ${C.green}`, borderRadius: 8, padding: "8px 12px",
          display: "flex", gap: 10, alignItems: "center" }}>
          <span style={{ flex: 1 }}>moved into your account: {moved}</span>
          <button onClick={() => setMoved(null)} style={btn({ fontSize: 10.5, padding: "2px 8px", color: C.mut })}>ok</button>
        </div>
      )}
      {ask && (
        <div role="dialog" aria-label="sign up to continue" data-testid="account-required"
          onClick={() => setAsk(null)}
          style={{ position: "fixed", inset: 0, zIndex: 60, background: "rgba(0,0,0,0.5)",
                   display: "flex", alignItems: "center", justifyContent: "center", padding: 16 }}>
          <div onClick={(e) => e.stopPropagation()}
            style={{ width: 340, maxWidth: "100%", background: C.bg, border: `1px solid ${C.line}`,
                     borderRadius: 10, padding: 16 }}>
            <div style={{ fontSize: 16, fontWeight: 650, marginBottom: 6 }}>
              {ask.charAt(0).toUpperCase() + ask.slice(1)} need an account
            </div>
            <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, lineHeight: 1.6, marginBottom: 12 }}>
              Free, one minute. Everything you did as a guest — runs, agents, caught lies —
              moves into the new workspace.
            </div>
            <IdentityLogin initialMode="signup" onDone={() => setAsk(null)} />
          </div>
        </div>
      )}
    </>
  );
}

/** "guest · 24 min left" — for the header. */
export function guestTimeLeft(expiresAt: string): string {
  const ms = new Date(expiresAt).getTime() - Date.now();
  if (isNaN(ms)) return "";
  if (ms <= 0) return "expired";
  const m = Math.ceil(ms / 60000);
  return `${m} min left`;
}
