// Config regression: replay the pinned corpus under a candidate config
// (regression-report mockup). Wired to /v1/regression — deterministic replay,
// no model calls.
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, ChevronDown, ChevronRight, Circle, ExternalLink, Loader2, Lock, Play } from "lucide-react";
import { api, RegressionReport, RegressionRow } from "../api";
import { C, MONO, btn } from "../theme";
import { navigate } from "../router";
import Coach from "../components/Coach";
import Tooltip from "../components/Tooltip";

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

// Org layer (EE): schedule the corpus + browse run history. Rendered locked
// (honest upsell, same pattern as the availability ladder) without a license.
function ScheduleAndHistory({ parseConfig }: { parseConfig: () => Record<string, unknown> | null }) {
  const qc = useQueryClient();
  const schedule = useQuery({ queryKey: ["regression-schedule"], queryFn: api.getRegressionSchedule });
  const ee = schedule.data?.ee_active === true;
  const history = useQuery({
    queryKey: ["regression-history"],
    queryFn: () => api.regressionHistory(20),
    enabled: ee,
  });
  const [hours, setHours] = useState("24");
  const save = useMutation({
    mutationFn: (enabled: boolean) => {
      const config = parseConfig();
      if (config === null) throw new Error("config above must be valid JSON");
      return api.putRegressionSchedule(enabled, Number(hours) || 24, config);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["regression-schedule"] });
    },
  });

  if (!schedule.data) return null;
  return (
    <div className="mb-8">
      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, letterSpacing: "0.08em", marginBottom: 8 }}>
        SCHEDULED CI · HISTORY
        <span style={{ marginLeft: 8, border: `1px solid ${C.line}`, borderRadius: 20, padding: "1px 6px", fontSize: 8.5 }}>
          Team
        </span>
      </div>
      {!ee ? (
        <div className="flex items-center gap-2 p-4" style={{ background: C.panel, border: `1px dashed ${C.line}`, borderRadius: 8 }}>
          <Lock size={13} color={C.dim} />
          <span style={{ fontFamily: MONO, fontSize: 11.5, color: C.dim }}>
            scheduled corpus runs + history are org features — add a license in
            Settings · manual runs stay free forever
          </span>
        </div>
      ) : (
        <>
          <div className="flex items-center gap-3 mb-3">
            <span style={{ fontFamily: MONO, fontSize: 11.5, color: schedule.data.enabled ? C.green : C.dim }}>
              {schedule.data.enabled
                ? `on — every ${schedule.data.interval_hours}h`
                : "off"}
              {schedule.data.last_run_ts ? ` · last ${schedule.data.last_run_ts.slice(0, 16)}` : ""}
            </span>
            <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>every</span>
            <input
              value={hours}
              onChange={(e) => setHours(e.target.value)}
              style={{
                width: 44, background: C.bg, border: `1px solid ${C.line}`, borderRadius: 5,
                color: C.text, fontFamily: MONO, fontSize: 11.5, padding: "4px 6px", outline: "none",
              }}
            />
            <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>h</span>
            <button onClick={() => save.mutate(true)} disabled={save.isPending}
              style={btn({ color: C.steel, fontSize: 11, padding: "6px 10px" })}>
              <CalendarClock size={12} /> Schedule with config above
            </button>
            {schedule.data.enabled && (
              <button onClick={() => save.mutate(false)} disabled={save.isPending}
                style={btn({ color: C.mut, fontSize: 11, padding: "6px 10px" })}>
                Disable
              </button>
            )}
            {save.isError && (
              <span style={{ fontFamily: MONO, fontSize: 11, color: C.red }}>
                {(save.error as Error).message}
              </span>
            )}
          </div>
          {(history.data ?? []).length > 0 && (
            <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
              {(history.data ?? []).map((h, i) => (
                <div key={i} className="flex items-center gap-3 px-4 py-2"
                  style={{ borderTop: i ? `1px solid ${C.line}` : "none", fontFamily: MONO, fontSize: 11 }}>
                  <Circle size={8} fill={h.safe_to_ship ? C.green : C.red} color={h.safe_to_ship ? C.green : C.red} />
                  <span style={{ color: C.dim, width: 122 }}>{h.created_ts.slice(0, 16)}</span>
                  <span style={{ color: C.dim, width: 72 }}>{h.source}</span>
                  <span style={{ color: h.safe_to_ship ? C.mut : C.text, flex: 1 }}>
                    {h.safe_to_ship
                      ? `safe — ${h.total} rows`
                      : `${h.regressed} regressed · ${h.escaped} escaped of ${h.total}`}
                  </span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

export default function Regression({ initialConfig }: { initialConfig?: string } = {}) {
  const [raw, setRaw] = useState(initialConfig ?? DEFAULT_CONFIG);
  const [parseError, setParseError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  const regression = useMutation({
    mutationFn: (config: Record<string, unknown>) => api.regression(config),
  });

  // Seed a two-sided corpus (must-block attack + must-pass legit flow) and drop
  // its matching config into the editor, so a fresh install can demonstrate the
  // regression CI with real data instead of an empty corpus.
  const seed = useMutation({
    mutationFn: () => api.seedAdapterRuns(),
    onSuccess: (r) => setRaw(JSON.stringify(r.config, null, 2)),
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
      <Coach id="regression" title="Regression — is this config safe to ship?">
        Replay a candidate config against your pinned corpus. It's two-sided:
        every <span style={{ color: C.text }}>must-block</span> attack should stay
        blocked and every <span style={{ color: C.text }}>must-pass</span> flow should
        keep passing. A config that blocks everything would pass a one-sided check —
        this catches that. Load the example corpus to see it work.
      </Coach>
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

      <div data-tour="regression-run" className="flex items-center gap-3 mt-2 mb-8">
        <Tooltip content="Replay every pinned run under the config above and report which attacks stayed blocked and which legit flows still pass.">
          <button onClick={run} disabled={regression.isPending} style={btn({ color: C.steel, fontSize: 12, padding: "8px 14px" })}>
            {regression.isPending ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />} Run regression
          </button>
        </Tooltip>
        <Tooltip content="Pins a must-block attack and a must-pass legit flow (adapter-fidelity) so the corpus has both sides to check against.">
          <button
            onClick={() => seed.mutate()}
            disabled={seed.isPending}
            style={btn({ color: C.mut, fontSize: 11, padding: "7px 12px" })}
          >
            {seed.isPending ? "seeding…" : "load example corpus"}
          </button>
        </Tooltip>
        {regression.isError && (
          <span style={{ fontFamily: MONO, fontSize: 11, color: C.red }}>
            {(regression.error as Error).message}
          </span>
        )}
      </div>

      <ScheduleAndHistory
        parseConfig={() => {
          try {
            const parsed: unknown = JSON.parse(raw);
            return parsed !== null && typeof parsed === "object" && !Array.isArray(parsed)
              ? (parsed as Record<string, unknown>)
              : null;
          } catch {
            return null;
          }
        }}
      />

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
