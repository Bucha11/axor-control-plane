// Scenario Delta: the governed-vs-ungoverned headline for a run (spec §8). The
// two sides are not two mockups — they are the two governance postures over the
// SAME recorded run. Ungoverned is what the agent's claim would have shipped
// unchallenged; governed is what Axor caught by checking that claim against the
// observed tool reality. Both come from the run's EvidenceCase, so the delta is
// real, not narrated.
import { EvidenceCaseDto } from "../api";
import { C, MONO } from "../theme";
import { deviationHeadline } from "./EvidenceCase";

function summarize(v: unknown): string {
  if (v == null) return "—";
  if (typeof v === "string") return v;
  try {
    const s = JSON.stringify(v);
    return s.length > 160 ? s.slice(0, 158) + "…" : s;
  } catch {
    return String(v);
  }
}

export default function ScenarioDelta({ c }: { c: EvidenceCaseDto }) {
  const attribution = c.fault_attribution[0];
  return (
    <div className="mt-4 p-4" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
      <div style={{ fontSize: 10, fontFamily: MONO, color: C.dim, letterSpacing: "0.1em", marginBottom: 12 }}>
        SCENARIO DELTA · GOVERNED vs UNGOVERNED
      </div>

      <div style={{ fontFamily: MONO, fontSize: 13, color: C.text, lineHeight: 1.5, marginBottom: 14 }}>
        Same run, two postures: ungoverned <span style={{ color: C.red }}>ships the fabrication</span>;
        governed <span style={{ color: C.green }}>catches it as an EvidenceCase</span>
        {attribution ? ` when ${attribution.tool_name} was deprived.` : "."}
      </div>

      <div className="flex gap-3" style={{ flexWrap: "wrap" }}>
        <div style={{ flex: "1 1 240px", minWidth: 220, border: `1px solid ${C.line}`, borderRadius: 6, padding: 12 }}>
          <div style={{ fontFamily: MONO, fontSize: 11, color: C.red, marginBottom: 6 }}>
            ungoverned — shipped to the user
          </div>
          <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, lineHeight: 1.5 }}>
            the agent’s claim stands unchecked:
            <div style={{ color: C.text, marginTop: 4 }}>{summarize(c.agent_claim)}</div>
          </div>
        </div>

        <div style={{ flex: "1 1 240px", minWidth: 220, border: `1px solid ${C.steel}`, borderRadius: 6, padding: 12 }}>
          <div style={{ fontFamily: MONO, fontSize: 11, color: C.green, marginBottom: 6 }}>
            governed — caught by Axor
          </div>
          <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, lineHeight: 1.5 }}>
            checked against observed reality:
            <div style={{ color: C.text, marginTop: 4 }}>{summarize(c.observed_reality)}</div>
            <div style={{ color: C.amber, marginTop: 6 }}>{deviationHeadline(c)}</div>
          </div>
        </div>
      </div>

      <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, marginTop: 12 }}>
        the delta is the product: without governance this run reads as a success; with it, the discrepancy is a receipt.
      </div>
    </div>
  );
}
