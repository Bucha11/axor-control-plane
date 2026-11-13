// Finishes a sign-in with GitHub / Google: the provider sent the browser back
// with a one-time code in the address, which is traded here for a session.
// Guest work is then claimed by GuestSession, as after any sign-in.
import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useApp } from "../store";
import { IdentityError, oauthExchange, takeOAuthReturn } from "../identity";
import { track } from "../funnel";
import { C, MONO, btn } from "../theme";

export default function OAuthReturn() {
  const setSession = useApp((s) => s.setSession);
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const back = takeOAuthReturn();
    if (!back) return;
    if (back.error) {
      setError(back.error);
      return;
    }
    if (!back.code) return;
    oauthExchange(back.code)
      .then((s) => {
        setSession(s.access_token, s.refresh_token, s.user.email);
        if (s.created) track("signup_completed", "oauth");
        void qc.invalidateQueries();
      })
      .catch((e) => setError(e instanceof IdentityError ? e.message : "sign-in failed"));
  }, [setSession, qc]);

  if (!error) return null;
  return (
    <div role="alert" data-testid="oauth-error" style={{
      position: "fixed", top: 12, left: "50%", transform: "translateX(-50%)", zIndex: 60,
      background: C.panel, border: `1px solid ${C.red}`, padding: "8px 14px",
      fontFamily: MONO, fontSize: 11.5, color: C.red, display: "flex", gap: 12, alignItems: "center",
    }}>
      {error}
      <button onClick={() => setError(null)} style={btn({ color: C.mut, fontSize: 10.5, padding: "2px 8px" })}>
        dismiss
      </button>
    </div>
  );
}
