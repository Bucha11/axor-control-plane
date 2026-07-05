// Config regression: replay the pinned corpus under a candidate config
// (regression-report mockup). Wired to /v1/regression — deterministic replay,
// no model calls.
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Circle, ExternalLink, Loader2, Play } from "lucide-react";
import { api, RegressionReport, RegressionRow } from "../api";
import { C, MONO, btn } from "../theme";
import { navigate } from "../router";

const DEFAULT_CONFIG = JSON.stringify(
  { allowed_tools: [], egress_sinks: [] },
  null,
  2,
);

function dot(res: RegressionRow["result"]): string {
  if (res === "regressed") return C.red;
  if (res === "escaped") return C.amber; // an attack no longer blocked — red-level severity, no design yet
  return C.green;
}

function label(res: RegressionRow["result"]): string {
  switch (res) {
    case "held": return "still blocked";
    case "passed": return "still passes";
    case "regressed": return "REGRESSED";
    case "escaped": return "ESCAPED";
  }
}

function headline(report: RegressionReport): React.ReactNode {
  if (report.safe_to_ship) {
    return <>Safe to ship: every attack still blocked, every legitimate flow still passes.</>;
  }
  const parts: string[] = [];
  if (report.regressed > 0) {
    parts.push(`${report.regressed} regression${report.regressed === 1 ? "" : "s"}`);
  }
  if (report.escaped > 0) {
    parts.push(`${report.escaped} escape${report.escaped === 1 ? "" : "s"}`);
  }
  return (
    <>
      <span style={{ color: C.red }}>{parts.join(" + ") || "Failure"}</span> — v2 changes governed behavior.
    </>
  );
}

export default function Regression({ initialConfig }: { initialConfig?: string } = {}) {
  const [raw, setRaw] = useState(initialConfig ?? DEFAULT_CONFIG);
  const [parseError, setParseError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  const regression = useMutation({
    mutationFn: (config: Record<string, unknown>) => api.regression(config),
  });

  const run = () => {
    setParseError(null);
    setOpen(null);
    let config: Record<string, unknown>;
    try {
      const parsed: unknown = JSON.parse(raw);
      if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("config must be a JSON object");
      }
      config = parsed as Record<string, unknown>;
    } catch (e) {
      setParseError(e instanceof Error ? e.message : String(e));
      return;
    }
    regression.mutate(config);
  };

  const report = regression.data;
  const held = report?.rows.filter((r) => r.result === "held").length ?? 0;
  const passed = report?.rows.filter((r) => r.result === "passed").length ?? 0;

  return (
    <div style={{ maxWidth: 640, margin: "0 auto" }}>
      <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginBottom: 6 }}>
        axor.config <span style={{ color: C.text }}>candidate</span> vs pinned corpus · deterministic replay, no model calls
      </div>

      <textarea
        value={raw}
        onChange={(e) => setRaw(e.target.value)}
        rows={6}
        spellCheck={false}
        className="w-full"
        style={{
          background: C.panel2, border: `1px solid ${C.line}`, borderRadius: 6,
          color: C.text, fontFamily: MONO, fontSize: 12, padding: 10,
          resize: "vertical", outline: "none",
        }}
      />
      {parseError && (
        <div className="mt-1" style={{ fontFamily: MONO, fontSize: 11, color: C.red }}>
          {parseError}
        </div>
      )}

      <div className="flex items-center gap-3 mt-2 mb-8">
        <button onClick={run} disabled={regression.isPending} style={btn({ color: C.steel, fontSize: 12, padding: "8px 14px" })}>
          {regression.isPending ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />} Run regression
        </button>
        {regression.isError && (
          <span style={{ fontFamily: MONO, fontSize: 11, color: C.red }}>
            {(regression.error as Error).message}
          </span>
        )}
      </div>

      {report && (
        report.rows.length === 0 ? (
          <div style={{ fontFamily: MONO, fontSize: 12.5, color: C.mut }}>
            No pinned traces. Run an experiment (Eval) — discrepancy-bearing traces auto-pin the must-block side; add legitimate flows as must-pass.
          </div>
        ) : (
          <>
            <h1 style={{ fontSize: 22, fontWeight: 650, lineHeight: 1.3, margin: "0 0 6px" }}>
              {headline(report)}
            </h1>
            <div style={{ fontFamily: MONO, fontSize: 12, color: C.mut, marginBottom: 20 }}>
              {held} attacks still blocked · {passed} legitimate flows still pass ·{" "}
              {report.regressed} regressed · {report.escaped} escaped
            </div>

            <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
              {report.rows.map((r, i) => {
                const hot = r.result === "regressed" || r.result === "escaped";
                const expandable = r.new_denial != null || r.first_divergence != null;
                const isOpen = open === r.run_id;
                return (
                  <div key={r.run_id} style={{ borderTop: i ? `1px solid ${C.line}` : "none" }}>
                    <div
                      onClick={() => setOpen(isOpen ? null : r.run_id)}
                      className="flex items-center gap-3 px-4 py-3"
                      style={{ cursor: expandable ? "pointer" : "default" }}
                    >
                      <Circle size={9} fill={dot(r.result)} color={dot(r.result)} />
                      <span style={{ fontFamily: MONO, fontSize: 10, color: C.dim, width: 74 }}>
                        {r.side.replaceAll("_", "-")}
                      </span>
                      <span style={{ fontFamily: MONO, fontSize: 12.5, color: hot ? C.text : C.mut, flex: 1 }}>
                        {r.label || r.run_id}
                      </span>
                      <span style={{
                        fontFamily: MONO, fontSize: 10.5,
                        fontWeight: hot ? 700 : 400,
                        color: r.result === "regressed" ? C.red : r.result === "escaped" ? C.amber : C.dim,
                      }}>
                        {label(r.result)}
                      </span>
                      {expandable
                        ? (isOpen ? <ChevronDown size={13} color={C.dim} /> : <ChevronRight size={13} color={C.dim} />)
                        : <span style={{ width: 13 }} />}
                    </div>
                    {isOpen && expandable && (
                      <div className="px-4 pb-3 flex flex-col gap-2" style={{ paddingLeft: 40 }}>
                        <span style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, lineHeight: 1.6 }}>
                          {r.new_denial
                            ? <>NEW denial at step {r.new_denial.seq}: {r.new_denial.reason} ({r.new_denial.category})</>
                            : <>first divergence at step {r.first_divergence}</>}
                          {" · "}{r.run_id}
                        </span>
                        <div className="flex items-center gap-2">
                          <button
                            onClick={() =>
                              navigate(`replay/${r.run_id}`, {
                                cursor: r.new_denial?.seq ?? r.first_divergence ?? 0,
                              })
                            }
                            style={btn({ color: C.steel, fontSize: 11, padding: "5px 10px" })}
                          >
                            <Play size={11} /> open in replay at divergence
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>

            <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 14, lineHeight: 1.7 }}>
              must-block = auto-pinned traces containing an EvidenceCase · must-pass = manually pinned legitimate flows.
              A corpus needs both sides — a config that blocks everything would pass a one-sided CI.
            </div>
          </>
        )
      )}
    </div>
  );
}
