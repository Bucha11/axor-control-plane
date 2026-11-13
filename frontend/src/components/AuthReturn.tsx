// What the identity service sends the browser back with, finished here:
// - ?axor_login=<code>      a GitHub / Google sign-in → traded for a session
//                           (guest work is then claimed by GuestSession)
// - ?axor_login_error=<why> that sign-in failed → said so
// - ?axor_verify=<token>    the link from the verification e-mail
// - ?axor_reset=<token>     the link from a reset e-mail → choose a new password
import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useApp } from "../store";
import { IdentityError, oauthExchange, resetPassword, takeAuthReturn, verifyEmail } from "../identity";
import { track } from "../funnel";
import { renewSession } from "../api";
import { C, MONO, btn } from "../theme";

const INPUT = {
  background: C.panel2, border: `1px solid ${C.line}`, borderRadius: 5, color: C.text,
  fontFamily: MONO, fontSize: 12, padding: "7px 10px", outline: "none",
} as const;

function why(e: unknown, fallback: string): string {
  return e instanceof IdentityError ? e.message : fallback;
}

export default function AuthReturn() {
  const setSession = useApp((s) => s.setSession);
  const qc = useQueryClient();
  const [note, setNote] = useState<{ text: string; ok: boolean } | null>(null);
  const [resetToken, setResetToken] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const back = takeAuthReturn();
    if (!back) return;
    if (back.error) setNote({ text: back.error, ok: false });
    if (back.reset) setResetToken(back.reset);
    if (back.verify) {
      verifyEmail(back.verify)
        .then((r) => {
          setNote({ text: `${r.email} is confirmed`, ok: true });
          // the access token says whether the address is confirmed (alert mail
          // is gated on it): fetch one that knows
          if (useApp.getState().refreshToken) void renewSession();
          void qc.invalidateQueries({ queryKey: ["identity-me"] });
        })
        .catch((e) => setNote({ text: why(e, "could not confirm the address"), ok: false }));
    }
    if (back.code) {
      oauthExchange(back.code)
        .then((s) => {
          setSession(s.access_token, s.refresh_token, s.user.email);
          if (s.created) track("signup_completed", "oauth");
          void qc.invalidateQueries();
        })
        .catch((e) => setNote({ text: why(e, "sign-in failed"), ok: false }));
    }
  }, [setSession, qc]);

  async function saveReset() {
    setBusy(true);
    try {
      await resetPassword(resetToken!, password);
      setResetToken(null);
      setPassword("");
      setNote({ text: "password changed — every other session was signed out; log in with the new one", ok: true });
    } catch (e) {
      setNote({ text: why(e, "could not change the password"), ok: false });
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      {note && (
        <div role="alert" data-testid="auth-note" style={{
          position: "fixed", top: 12, left: "50%", transform: "translateX(-50%)", zIndex: 60,
          background: C.panel, border: `1px solid ${note.ok ? C.green : C.red}`, padding: "8px 14px",
          fontFamily: MONO, fontSize: 11.5, color: note.ok ? C.green : C.red,
          display: "flex", gap: 12, alignItems: "center",
        }}>
          {note.text}
          <button onClick={() => setNote(null)} style={btn({ color: C.mut, fontSize: 10.5, padding: "2px 8px" })}>
            dismiss
          </button>
        </div>
      )}
      {resetToken && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.55)", zIndex: 55,
          display: "flex", alignItems: "center", justifyContent: "center" }}>
          <form role="dialog" aria-label="choose a new password"
            onSubmit={(e) => { e.preventDefault(); if (password.length >= 8 && !busy) void saveReset(); }}
            style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8, padding: 18,
              display: "flex", flexDirection: "column", gap: 10, width: 340 }}>
            <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, letterSpacing: "0.08em" }}>
              CHOOSE A NEW PASSWORD
            </div>
            <input type="password" autoComplete="new-password" aria-label="new password"
              placeholder="new password (8+ characters)" value={password}
              onChange={(e) => setPassword(e.target.value)} style={INPUT} />
            <div className="flex items-center gap-2">
              <button type="submit" disabled={password.length < 8 || busy}
                style={btn({ color: C.steel, borderColor: C.steel, fontSize: 11, padding: "5px 14px" })}>
                {busy ? "…" : "set password"}
              </button>
              <button type="button" onClick={() => setResetToken(null)}
                style={btn({ color: C.dim, fontSize: 10.5, padding: "5px 10px" })}>
                cancel
              </button>
            </div>
          </form>
        </div>
      )}
    </>
  );
}
