// Policy as the kernel config Replay's what-if and Safe to ship? check: per
// tool its kind, the arguments that decide (the taint check narrows to them),
// the values always fine for each, and how bad it is if it goes wrong.
import type { ToolClass } from "./store";

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
