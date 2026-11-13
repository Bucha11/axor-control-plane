// The sign-up funnel, first-party and anonymous (backend: axor_backend/funnel.py).
//
// A step is posted with a random id for this tab — no account, no email, no
// IP kept. Each step is sent once per tab. Do Not Track is honoured: with it
// on, nothing is sent at all.

export type FunnelEvent =
  | "visit" | "guest_started" | "demo_fleet_loaded" | "lie_caught"
  | "signup_prompt_shown" | "signup_completed" | "guest_claimed";

const TAB_KEY = "axor-funnel-tab";
const SENT_KEY = "axor-funnel-sent";

function tabId(): string {
  try {
    let id = window.sessionStorage.getItem(TAB_KEY);
    if (!id) {
      id = "t" + Array.from(crypto.getRandomValues(new Uint8Array(12)))
        .map((b) => b.toString(16).padStart(2, "0")).join("");
      window.sessionStorage.setItem(TAB_KEY, id);
    }
    return id;
  } catch {
    return "";
  }
}

function doNotTrack(): boolean {
  const nav = navigator as Navigator & { globalPrivacyControl?: boolean };
  return nav.doNotTrack === "1" || nav.globalPrivacyControl === true;
}

export function track(event: FunnelEvent, detail = ""): void {
  if (doNotTrack()) return;
  const id = tabId();
  if (!id) return;
  const clean = detail.toLowerCase().replace(/[^a-z0-9 _.-]/g, "").slice(0, 64);
  const key = `${event}:${clean}`;
  try {
    const sent = new Set<string>(JSON.parse(window.sessionStorage.getItem(SENT_KEY) || "[]"));
    if (sent.has(key)) return;
    sent.add(key);
    window.sessionStorage.setItem(SENT_KEY, JSON.stringify([...sent]));
  } catch {
    /* storage unavailable: still send, at worst twice */
  }
  void fetch("/v1/funnel", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ event, session_id: id, detail: clean }),
    keepalive: true,
  }).catch(() => undefined);
}
