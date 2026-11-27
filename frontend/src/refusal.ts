// Why a call was refused, in words — the kernel's own sentence is for
// "details". One place, so the activity feed, a run's timeline and Replay say
// the same thing for the same refusal.

export function refusalWords(rawIn: string, gate = ""): string {
  // what to do about it ("run it yourself", "allow it in .axor/rules.json") is
  // said beside the reason, not inside it
  const raw = rawIn.replace(/^axor:\s*/, "").split(/\s+If you want it, run it yourself:|\s+Read it; if you trust it/)[0];
  if (/Refused by the axor rule/.test(raw)) return raw.split(" Refused by the axor rule")[0];
  if (/Your team's policy refuses it/.test(raw)) return raw.split(" Your team's policy refuses it")[0];
  if (/turned off from the Control Plane|paused from the Control Plane|stopped from the Control Plane/.test(raw)) return raw;
  if (gate === "taint_floor" || gate === "taint_enforcement" || /taint enforcement/.test(raw)) {
    return /confidentiality|secret|sensitive value/.test(raw) && !/tainted\/sensitive/.test(raw)
      ? "it would have sent out a secret the agent read"
      : "what it was going to use came from text from outside";
  }
  if (gate === "carrier" || gate === "carrier_gate" || /^carrier gate/.test(raw)) {
    return "it would have followed an instruction that came from outside text";
  }
  if (gate === "budget" || /budget/.test(raw)) return "the call budget was spent";
  if (gate === "ssrf" || gate === "ssrf_gate") return "it pointed at an internal address";
  if (gate === "value_policies" || gate === "value_policy") return "the value is not one of the allowed ones";
  if (/not in the capability table/.test(raw)) return "this tool is not on the allowed list";
  if (gate === "capability") return "this tool is not allowed here";
  return raw || "refused";
}

type Scope = "read" | "ingest" | "operate" | "admin";

/** Who in a workspace holds a scope (backend auth.ROLE_SCOPES): a viewer reads;
 *  a member and an admin also ingest and operate; only the owner administers. */
export function whoCan(need: string): string {
  if (need === "admin") return "only the owner";
  if (need === "read" || need === "read|ingest") return "anyone in the workspace";
  return "an owner, admin or member";
}

/** Who the caller is, by the scopes they hold. */
export function whoIs(have: readonly string[]): string {
  if (have.includes("admin")) return "the owner";
  if (have.includes("operate")) return "a member or admin";
  if (have.includes("read")) return "a viewer";
  return "this sign-in";
}

/** A scope refusal ({"error": "forbidden", "need", "have"}) in words: who the
 *  caller is and who can do it, never the bare word "forbidden" (QA-195). */
export function scopeWords(need: Scope | string, have: readonly string[]): string {
  return `${whoIs(have)} can't change this — ${whoCan(need)} can`;
}

/** A request's error as a person reads it: "403 {\"detail\": \"…\"}" → "…". */
export function errorWords(message: string): string {
  const m = message.match(/^(\d{3})\s+(\{[\s\S]*\})$/);
  if (!m) return message;
  try {
    const body = JSON.parse(m[2]) as { detail?: unknown; error?: unknown; need?: unknown; have?: unknown };
    if (body.error === "forbidden" && typeof body.need === "string" && body.detail === undefined) {
      return scopeWords(body.need, Array.isArray(body.have) ? body.have.map(String) : []);
    }
    const d = body.detail ?? body.error;
    if (typeof d === "string") return d;
    if (d && typeof d === "object" && typeof (d as { detail?: unknown }).detail === "string") {
      return (d as { detail: string }).detail;
    }
  } catch { /* not JSON after all */ }
  return message;
}
