// The EvidenceCase — the product's primary artifact (spec section 5). Every
// screen produces, displays, or aggregates one; this is the shared receipt, and
// crucially it is a NAVIGATION HUB, not a dead end: it links to the moment in
// Replay, and it can leave the product (share permalink + export), per section
// 8.3. Wherever a case is shown, it is shown through this component.
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Check, ExternalLink, Play, Share2 } from "lucide-react";
import { api, EvidenceCaseDto } from "../api";
import { navigate } from "../router";
import { C, MONO } from "../theme";

const HEADLINE: Record<string, string> = {
  fabricated_tool_result: "fabricated a tool result",
  corrupted_retrieval_used: "surfaced poisoned retrieval",
  direct_policy_violation: "executed an injected instruction",
  undisclosed_tool_substitution: "hid a tool substitution",
  budget_misreport: "misreported its budget",
};

export function deviationHeadline(c: EvidenceCaseDto): string {
  return c.deviation ? (HEADLINE[c.deviation] ?? c.deviation.replaceAll("_", " ")) : "deviated";
}

function render(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value);
}

export default function EvidenceCase({
  runId,
  caseIndex,
  c,
  replayStep,
}: {
  runId: string;
  caseIndex: number;
  c: EvidenceCaseDto;
  // the trace step this case points at (for "Replay this moment")
  replayStep?: number;
}) {
  const [shareUrl, setShareUrl] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const share = useMutation({
    mutationFn: () => api.shareCase(runId, caseIndex),
    onSuccess: (r) => {
      const absolute = `${window.location.origin}${r.url}`;
      setShareUrl(absolute);
      void navigator.clipboard?.writeText(absolute).then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      });
    },
  });

  return (
    <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8, overflow: "hidden" }}>
      <div className="p-4" style={{ borderBottom: `1px solid ${C.line}` }}>
        <div style={{ fontSize: 10, fontFamily: MONO, color: C.dim, letterSpacing: "0.1em", marginBottom: 6 }}>
          WHAT HAPPENED
        </div>
        <div style={{ fontFamily: MONO, fontSize: 13, color: C.text }}>{render(c.observed_reality)}</div>
      </div>
      <div className="p-4">
        <div style={{ fontSize: 10, fontFamily: MONO, color: C.dim, letterSpacing: "0.1em", marginBottom: 6 }}>
          WHAT THE AGENT SAID
        </div>
        <div style={{ fontFamily: MONO, fontSize: 13, color: C.text }}>{render(c.agent_claim)}</div>
      </div>
      <div
        className="px-4 py-3 flex items-center justify-between flex-wrap gap-2"
        style={{ background: "rgba(229,72,77,0.06)", borderTop: `1px solid ${C.line}` }}
      >
        <span style={{ fontFamily: MONO, fontSize: 11, color: C.red, fontWeight: 700 }}>
          {(c.deviation ?? "no deviation").toUpperCase().replaceAll("_", " ")} · {c.verdict_source} ·
          confidence {c.confidence}
        </span>
        <div className="flex gap-2 items-center">
          <button
            onClick={() => navigate(`replay/${runId}`, { cursor: replayStep })}
            style={action(C.steel)}
          >
            <Play size={11} /> Replay this moment
          </button>
          <button onClick={() => share.mutate()} style={action(copied ? C.green : C.mut)}>
            {copied ? <Check size={11} /> : <Share2 size={11} />} {copied ? "link copied" : "Share"}
          </button>
          <a href={api.exportUrl(runId, caseIndex)} target="_blank" rel="noreferrer" style={{ ...action(C.mut), textDecoration: "none" }}>
            <ExternalLink size={11} /> Export
          </a>
        </div>
      </div>
      {shareUrl && (
        <div className="px-4 py-2" style={{ borderTop: `1px solid ${C.line}` }}>
          <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>
            revocable permalink · observations only, no raw bodies · {shareUrl}
          </span>
        </div>
      )}
    </div>
  );
}

function action(color: string): React.CSSProperties {
  return {
    display: "flex",
    alignItems: "center",
    gap: 6,
    background: "none",
    border: `1px solid ${C.line}`,
    borderRadius: 5,
    color,
    fontFamily: MONO,
    fontSize: 11,
    padding: "5px 10px",
    cursor: "pointer",
  };
}
