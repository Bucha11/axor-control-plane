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
import { api, guestRetryAfter, sessionSettled, tabSignedOut } from "../api";
import { isGuestToken, useApp } from "../store";
import { C, MONO, btn } from "../theme";
import { IdentityLogin } from "../tabs/Settings";
import { navigate } from "../router";
import { track } from "../funnel";

const LABELS: [string, string][] = [
  ["runs", "run"], ["desired_state", "agent"], ["probe_reports", "health check"],
  ["reputation_snapshots", "reputation snapshot"], ["heal_attempts", "repair"],
];

function describeMoved(moved: Record<string, number>): string {
  const parts = LABELS
    .filter(([table]) => moved[table])
    .map(([table, word]) => `${moved[table]} ${word}${moved[table] === 1 ? "" : "s"}`);
  // only what can be named: rows behind the examples a guest looked at (their
  // events, their evidence) are not work the guest did
  return parts.length ? parts.join(", ") : "";
}

// `invite`: the invitation page, where the guests' "too many visitors" notice
// is about nothing the person came for (QA-070)
export default function GuestSession({ invite = false }: { invite?: boolean } = {}) {
  const qc = useQueryClient();
  const apiToken = useApp((s) => s.apiToken);
  const refreshToken = useApp((s) => s.refreshToken);
  const guestTokens = useApp((s) => s.guestTokens);
  const sessionEnded = useApp((s) => s.sessionEnded);
  const [ask, setAsk] = useState<string | null>(null);
  const [askText, setAskText] = useState<{ title?: string; why?: string }>({});
  const [moved, setMoved] = useState<string | null>(null);
  // the plane refused a guest session for now (too many from this address):
  // said, and tried again when it allows — not every screen failing silently
  const [busyFor, setBusyFor] = useState(0);
  // this tab has no sign-in and the plane needs one (QA-170)
  const [signedOut, setSignedOut] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    track("visit");
  }, []);

  // nothing to authenticate with: start as a guest, if the plane offers it
  // (sessionSettled does, once for every request waiting on it)
  useEffect(() => {
    if (apiToken) return;
    let alive = true;
    let retry: ReturnType<typeof setTimeout> | undefined;
    void sessionSettled(attempt > 0).then(() => {
      if (!alive) return;
      setSignedOut(tabSignedOut());
      if (useApp.getState().apiToken) {
        setBusyFor(0);
        void qc.invalidateQueries();
        return;
      }
      const wait = guestRetryAfter();
      setBusyFor(wait);
      if (wait) retry = setTimeout(() => setAttempt((n) => n + 1), wait * 1000);
    });
    return () => {
      alive = false;
      if (retry) clearTimeout(retry);
    };
  }, [apiToken, qc, attempt]);

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
      const said = describeMoved(total);
      if (said) {
        setMoved(said);
        track("guest_claimed");
      }
      void qc.invalidateQueries();
    });
  }, [apiToken, refreshToken, guestTokens, qc]);

  // a guest reached an account-only feature
  useEffect(() => {
    const on = (e: Event) => {
      const detail = (e as CustomEvent<{ feature?: string; title?: string; why?: string }>).detail;
      if (isGuestToken(useApp.getState().apiToken)) {
        setAsk(detail?.feature ?? "this");
        setAskText({ title: detail?.title, why: detail?.why });
        track("signup_prompt_shown", detail?.feature ?? "");
      }
    };
    window.addEventListener("axor:account-required", on);
    return () => window.removeEventListener("axor:account-required", on);
  }, []);

  return (
    <>
      {sessionEnded && (
        <div data-testid="session-ended" style={{
          maxWidth: 1040, margin: "0 auto 12px", fontFamily: MONO, fontSize: 11.5, color: C.amber,
          border: `1px solid ${C.amber}`, borderRadius: 8, padding: "8px 12px",
          display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <span style={{ flex: 1 }}>your session ended — sign in again</span>
          <button onClick={() => navigate("settings", { tab: "workspace" })}
            style={btn({ fontSize: 10.5, padding: "2px 8px", color: C.steel, borderColor: C.steel })}>sign in</button>
          <button onClick={() => useApp.getState().setSessionEnded(false)}
            style={btn({ fontSize: 10.5, padding: "2px 8px", color: C.mut })}>ok</button>
        </div>
      )}
      {signedOut && !apiToken && !sessionEnded && busyFor === 0 && !invite && (
        <div data-testid="tab-signed-out" style={{
          maxWidth: 1040, margin: "0 auto 12px", fontFamily: MONO, fontSize: 11.5, color: C.amber,
          border: `1px solid ${C.amber}`, borderRadius: 8, padding: "8px 12px",
          display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <span style={{ flex: 1 }}>this tab is signed out — sign in to see your workspace (each tab signs in on its own)</span>
          <button onClick={() => navigate("settings", { tab: "workspace" })}
            style={btn({ fontSize: 10.5, padding: "2px 8px", color: C.steel, borderColor: C.steel })}>sign in</button>
        </div>
      )}
      {busyFor > 0 && !apiToken && !invite && (
        <div data-testid="guest-busy" style={{
          maxWidth: 1040, margin: "0 auto 12px", fontFamily: MONO, fontSize: 11.5, color: C.amber,
          border: `1px solid ${C.amber}`, borderRadius: 8, padding: "8px 12px", lineHeight: 1.6 }}>
          Too many new visitors from your network right now — the examples open in about{" "}
          {busyFor < 60 ? `${busyFor} s` : "a minute"}, by themselves. Or sign up: an account is not limited.
        </div>
      )}
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
              {askText.title ?? `${ask.charAt(0).toUpperCase() + ask.slice(1)} need an account`}
            </div>
            {askText.why && (
              <div style={{ fontSize: 13, color: C.text, lineHeight: 1.5, marginBottom: 8 }}>{askText.why}</div>
            )}
            <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, lineHeight: 1.6, marginBottom: 12 }}>
              Free, one minute. Everything you did as a guest — runs, agents, incidents —
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
