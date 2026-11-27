// An invitation to a workspace (#/invite/<token>): what it is for, and joining —
// with a new account, an account one already has, or the one signed in now.
import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { localDate } from "../time";
import { IdentityError, inviteInfo, login, signupInvited } from "../identity";
import { navigate } from "../router";
import { useApp } from "../store";
import { C, MONO, btn } from "../theme";

const INPUT = {
  background: C.bg, border: `1px solid ${C.line}`, borderRadius: 5, color: C.text,
  fontFamily: MONO, fontSize: 12, padding: "8px 10px", outline: "none", width: "100%",
} as const;

export default function Invite({ token }: { token: string }) {
  const qc = useQueryClient();
  const info = useQuery({ queryKey: ["invite", token], queryFn: () => inviteInfo(token), retry: false });
  const identityEmail = useApp((s) => s.identityEmail);
  const setSession = useApp((s) => s.setSession);
  const [mode, setMode] = useState<"signup" | "login">("signup");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // joined: straight to connecting their own machine, for what the team does
  const joined = async (access: string, refresh: string, who: string) => {
    setSession(access, refresh, who);
    void qc.invalidateQueries();
    const journey = await api.workspace().then((w) => w.journey).catch(() => null);
    navigate("connect", journey ? { for: journey } : {});
  };
  const join = async (how: "accept" | "signup" | "login") => {
    setBusy(true);
    setError(null);
    try {
      if (how === "accept") {
        const s = await api.acceptInvite(token);
        joined(s.access_token, s.refresh_token, s.user.email);
      } else if (how === "signup") {
        const s = await signupInvited(email, password, token);
        joined(s.access_token, s.refresh_token, s.user.email);
      } else {
        const first = await login(email, password);
        setSession(first.access_token, first.refresh_token, first.user.email);
        const s = await api.acceptInvite(token);
        joined(s.access_token, s.refresh_token, s.user.email);
      }
    } catch (e) {
      setError(e instanceof IdentityError ? e.message : (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  // the invitation names the address: it is filled in, not typed again
  const forEmail = info.data?.for_email ?? null;
  useEffect(() => {
    if (forEmail) setEmail((cur) => cur || forEmail);
  }, [forEmail]);

  if (info.isError) {
    return (
      <div data-testid="invite-gone" style={{ maxWidth: 480, margin: "40px auto", fontSize: 14, color: C.mut, lineHeight: 1.6 }}>
        <div style={{ fontSize: 18, color: C.text, fontWeight: 650, marginBottom: 6 }}>This invitation can't be used</div>
        {(info.error as Error).message}. Ask whoever sent it for a new one.
      </div>
    );
  }
  if (!info.data) return null;
  const i = info.data;
  return (
    <div data-testid="invite" style={{ maxWidth: 480, margin: "40px auto" }}>
      <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 6px" }}>Join {i.org_name || "a workspace"}</h1>
      <div style={{ fontSize: 14, color: C.mut, lineHeight: 1.6, marginBottom: 20 }}>
        You're invited as <span style={{ color: C.text }}>{i.role}</span>
        {i.for_email ? <> — for <span style={{ color: C.text }}>{i.for_email}</span></> : null}. The link works once,
        until {localDate(i.expires_ts)}.
      </div>
      {identityEmail ? (
        <div className="flex flex-col" style={{ gap: 10 }}>
          <button onClick={() => void join("accept")} disabled={busy} data-testid="invite-join"
            style={btn({ color: C.text, borderColor: C.steel, fontSize: 12, padding: "8px 14px" })}>
            {busy ? "…" : `join as ${identityEmail}`}
          </button>
        </div>
      ) : (
        <form onSubmit={(e) => { e.preventDefault(); void join(mode); }} className="flex flex-col" style={{ gap: 10 }}>
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder={i.for_email ?? "e-mail"}
            aria-label="email" style={INPUT} autoComplete="email" />
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="password"
            aria-label="password" style={INPUT} autoComplete={mode === "signup" ? "new-password" : "current-password"} />
          <button type="submit" disabled={busy || !email || password.length < 8} data-testid="invite-join"
            style={btn({ color: C.text, borderColor: C.steel, fontSize: 12, padding: "8px 14px" })}>
            {busy ? "…" : mode === "signup" ? "create my account and join" : "log in and join"}
          </button>
          <button type="button" onClick={() => setMode(mode === "signup" ? "login" : "signup")}
            style={{ background: "none", border: "none", padding: 0, cursor: "pointer", fontFamily: MONO, fontSize: 11.5, color: C.steel, textAlign: "left" }}>
            {mode === "signup" ? "already have an account? log in" : "new here? create an account"}
          </button>
        </form>
      )}
      {error && <div data-testid="invite-error" style={{ fontFamily: MONO, fontSize: 11.5, color: C.red, marginTop: 10 }}>{error}</div>}
    </div>
  );
}
