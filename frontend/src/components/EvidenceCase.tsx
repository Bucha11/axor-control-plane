// The EvidenceCase — the product's primary artifact (spec section 5). Every
// screen produces, displays, or aggregates one; this is the shared receipt, and
// crucially it is a NAVIGATION HUB, not a dead end: it links to the moment in
// Replay, and it can leave the product (share permalink + export), per section
// 8.3. Wherever a case is shown, it is shown through this component.
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Check, ExternalLink, FlaskConical, Play, Share2 } from "lucide-react";
import { api, EvidenceCaseDto } from "../api";
import CausalSubgraph from "./CausalSubgraph";
import { navigate } from "../router";
import { C, MONO } from "../theme";
import Tooltip from "./Tooltip";

const HEADLINE: Record<string, string> = {
  fabrication_contained: "contained a fabrication at the boundary",
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

// Optional deep link to a running Axor Lab instance (VITE_LAB_URL) — shown
// next to the downloaded incident package so the funnel is one click.
const LAB_URL =
  (import.meta as unknown as { env?: Record<string, string> }).env?.VITE_LAB_URL;

function downloadJson(name: string, value: unknown): void {
  const blob = new Blob([JSON.stringify(value, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
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
  // CP → Lab funnel state: null = untouched, [] = exported OK, else the honest
  // list of reasons the run is not convertible to a Lab incident package.
  const [labReasons, setLabReasons] = useState<string[] | null>(null);
  const [labExported, setLabExported] = useState(false);

  const labExport = useMutation({
    mutationFn: () => api.labPackage(runId),
    onSuccess: (r) => {
      if (r.ok) {
        downloadJson(`axor-lab-incident-${runId}.json`, r.pkg);
        setLabReasons(null);
        setLabExported(true);
      } else {
        setLabExported(false);
        setLabReasons(r.reasons);
      }
    },
  });

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
          <Tooltip content="Jump to the exact step in Replay where this discrepancy happened — scrub around it, fork a counterfactual.">
            <button
              onClick={() => navigate(`replay/${runId}`, { cursor: replayStep })}
              style={action(C.steel)}
            >
              <Play size={11} /> Replay this moment
            </button>
          </Tooltip>
          <Tooltip content="Copy a revocable permalink to just this case — one page, scoped token, no access to the rest of the workspace. Revoke it any time.">
            <button onClick={() => share.mutate()} style={action(copied ? C.green : C.mut)}>
              {copied ? <Check size={11} /> : <Share2 size={11} />} {copied ? "link copied" : "Share"}
            </button>
          </Tooltip>
          <Tooltip content="A self-contained HTML receipt — observations, labels and verdicts only, never raw request/response bodies.">
            <a href={api.exportUrl(runId, caseIndex)} target="_blank" rel="noreferrer" style={{ ...action(C.mut), textDecoration: "none" }}>
              <ExternalLink size={11} /> Export
            </a>
          </Tooltip>
          <Tooltip content="The same receipt as a single-page PDF — for tickets, audits, and people who print things.">
            <a href={api.exportUrl(runId, caseIndex, "pdf")} target="_blank" rel="noreferrer" style={{ ...action(C.mut), textDecoration: "none" }}>
              <ExternalLink size={11} /> PDF
            </a>
          </Tooltip>
          <Tooltip content="Download this run as an axor-lab-incident/v1 package (trace + scenario + manifests + recorded condition) — import it with `axor-lab import-incident` to replay, pin and test policies against the incident.">
            <button
              onClick={() => labExport.mutate()}
              disabled={labExport.isPending}
              style={action(labExported ? C.green : C.mut)}
            >
              <FlaskConical size={11} />{" "}
              {labExport.isPending ? "exporting…" : labExported ? "exported" : "Export for Lab"}
            </button>
          </Tooltip>
          {LAB_URL && (
            <a
              href={`${LAB_URL.replace(/\/$/, "")}/#/import`}
              target="_blank"
              rel="noreferrer"
              style={{ ...action(C.steel), textDecoration: "none" }}
            >
              Open in Axor Lab →
            </a>
          )}
        </div>
      </div>
      {labExport.isError && (
        <div className="px-4 py-2" style={{ borderTop: `1px solid ${C.line}` }}>
          <span style={{ fontFamily: MONO, fontSize: 11, color: C.red }}>
            lab export failed: {(labExport.error as Error).message}
          </span>
        </div>
      )}
      {labReasons && (
        <div className="px-4 py-3" style={{ borderTop: `1px solid ${C.line}` }}>
          <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, letterSpacing: "0.1em", marginBottom: 6 }}>
            NOT CONVERTIBLE TO A LAB INCIDENT
          </div>
          {labReasons.map((reason, i) => (
            <div key={i} style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, lineHeight: 1.6 }}>
              · {reason}
            </div>
          ))}
        </div>
      )}
      {/* Multi-agent case (spec v2 Ch.3): the causal subgraph derives on open.
          Renders nothing for size-1 — the receipt above IS the v0.13 case. */}
      {c.anchor && <CausalSubgraph runId={runId} anchor={c.anchor} />}
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
