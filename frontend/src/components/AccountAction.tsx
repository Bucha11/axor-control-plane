// An action whose value is what an account is for — keep an incident as a
// test, get mail when an agent goes wrong — offered where the thing happens.
// A guest is asked to sign up right there, told what it gets them (and that
// their guest work comes along); anyone else just gets the action. Sign-up is
// asked for at the moment it pays off, not by a banner.
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api } from "../api";
import { navigate } from "../router";
import { isGuestToken, useApp } from "../store";
import { C, MONO } from "../theme";

export function askToSignUp(feature: string, title: string, why: string): void {
  window.dispatchEvent(new CustomEvent("axor:account-required", { detail: { feature, title, why } }));
}

function useGuest(): boolean {
  return isGuestToken(useApp((s) => s.apiToken));
}

const link = (color: string) => ({
  background: "none", border: "none", padding: 0, cursor: "pointer",
  fontFamily: MONO, fontSize: 11, color,
}) as const;

/** "keep it as a test": pin a run that caught a lie, so every policy change
 * is checked against it before it ships (Safe to ship?). */
export function KeepAsTest({ runId, testId = "keep-as-test" }: { runId: string; testId?: string }) {
  const guest = useGuest();
  const pin = useMutation({ mutationFn: () => api.pin(runId, "must_block", "kept from an incident") });
  if (pin.isSuccess) {
    return (
      <span data-testid={testId} style={{ fontFamily: MONO, fontSize: 11, color: C.green }}>
        ✓ kept as a test ·{" "}
        <button onClick={() => navigate("prevent")} style={link(C.steel)}>Safe to ship? →</button>
      </span>
    );
  }
  return (
    <span data-testid={testId}>
      <button
        onClick={(e) => {
          e.stopPropagation();
          if (guest) {
            askToSignUp("tests", "Keep it as a test",
              "Every change to your agent's policy is checked against the lies it was caught in — before it ships.");
            return;
          }
          pin.mutate();
        }}
        style={link(C.steel)}>
        keep it as a test →
      </button>
      {pin.isError && <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.red }}> {(pin.error as Error).message}</span>}
    </span>
  );
}

/** "email me": mail when an agent drifts, is caught making something up, or
 * climbs a degradation level — to the signed-in person's own address. */
export function AlertMe({ testId = "alert-me", label = "email me when my agent goes wrong →" }: {
  testId?: string; label?: string;
}) {
  const guest = useGuest();
  const email = useApp((s) => s.identityEmail);
  const [note, setNote] = useState<string | null>(null);
  const sub = useMutation({
    mutationFn: () => api.subscribeNotifications(`mailto:${email}`,
      ["behavioral_drift", "evidence_run", "level_transition_up"]),
    onSuccess: () => setNote(`✓ alerts go to ${email}`),
    onError: (e: Error) => setNote(e.message),
  });
  return (
    <span data-testid={testId}>
      <button
        onClick={(e) => {
          e.stopPropagation();
          if (guest) {
            askToSignUp("alerts", "Get an email when your agent goes wrong",
              "When it drifts, is caught making something up, or its level climbs — you hear about it without watching this page.");
            return;
          }
          if (!email) { navigate("settings"); return; }
          sub.mutate();
        }}
        style={link(C.steel)}>
        {label}
      </button>
      {note && <span style={{ fontFamily: MONO, fontSize: 10.5, color: sub.isError ? C.red : C.green }}> {note}</span>}
    </span>
  );
}

/** The plain ask, for the end of an example: what an account gives on your own agent. */
export function SignUpForYourAgent({ testId = "signup-own-agent" }: { testId?: string }) {
  const guest = useGuest();
  if (!guest) return null;
  return (
    <div data-testid={testId} style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginTop: 10, lineHeight: 1.6 }}>
      want this on your own agent? an account is free: 1 production agent, alerts by email, incidents kept
      as tests — and what you did here comes with you.{" "}
      <button onClick={() => askToSignUp("your workspace", "Sign up — free",
        "1 production agent, alerts by email, incidents kept as tests.")} style={link(C.steel)}>
        sign up →
      </button>
    </div>
  );
}
