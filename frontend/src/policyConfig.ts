// Policy as the kernel config Replay's what-if and Safe to ship? check: per
// tool its kind, the arguments that decide (the taint check narrows to them),
// the values always fine for each, and how bad it is if it goes wrong.
import type { ToolClass } from "./store";

// A tool manifest's `untrusted_fields`: the parts of the tool's answer that are
// outside text. Every surface that writes manifests says it the same way, so a
// tool marked on Connect and the same tool marked in Policy cannot compile to
// two different things.
export function untrustedFields(c: Pick<ToolClass, "outside" | "outsideFields"> | undefined): string[] {
  if (!c?.outside) return [];
  const fields = (c.outsideFields ?? []).map((f) => f.trim()).filter(Boolean);
  return fields.length ? fields.map((f) => (f.startsWith("result") ? f : `result.${f}`)) : ["result.*"];
}

export function policyRules(policy: Record<string, ToolClass>): Record<string, unknown> {
  return {
    version: "axor-config/1",
    default: "DENY",
    sinks: Object.fromEntries(Object.entries(policy).map(([name, c]) => {
      const trusted = Object.fromEntries(Object.entries(c.trusted ?? {}).filter(([, v]) => v.length));
      return [name, {
        consequence_class: c.klass,
        ...(c.driving?.length ? { driving_args: c.driving } : {}),
        ...(Object.keys(trusted).length ? { trusted_sets: trusted } : {}),
        ...(c.consequence ? { consequence: c.consequence } : {}),
      }];
    })),
  };
}

// What a tool most likely does, from its name — the starting guess for a tool
// added by hand, which the person then checks. Everything used to start as
// "reads", so a forgotten send_email was policy's idea of a read.
export function guessKlass(name: string): ToolClass["klass"] {
  const n = name.toLowerCase();
  if (/(^|_|\b)(exec|shell|bash|terminal|eval|run_?code|run_?command|python)(_|\b|$)/.test(n)) return "EXEC";
  // a read named as one stays a read: read_email, get_messages, list_posts
  if (/^(read|get|list|search|fetch|lookup|find|query|view|show|describe)(_|$)/.test(n)) return "READ";
  if (/(send|e?mail|post|notify|slack|publish|upload|webhook|tweet|sms|message|share|export|forward|reply)/.test(n)) return "EXPORT";
  if (/(create|update|delete|remove|write|insert|issue|refund|pay|charge|transfer|cancel|^set_|_set_|put|patch|save|book|approve|grant|revoke)/.test(n)) return "WRITE";
  return "READ";
}
