// Replay: a run, step by step (/v1/replay/{run_id}). Every step in words — a
// tool asked, a tool answering (marked when its text came from outside), a
// call refused and why — where a call's input came from, as a chain of tools,
// and "what if": the same recorded steps decided again under other rules
// (/v1/replay/{run}/counterfactual — no model call, deterministic).
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ScrubberPayload, ScrubberStep } from "../api";
import { navigate } from "../router";
import { useApp } from "../store";
import { incidentWhat } from "./Evidence";
import { LAST_SEARCH } from "./ReplaySearch";
import { guessKlass, toolsFromSteps } from "../toolGuess";
import { policyRules } from "../policyConfig";
import { C, MONO, btn, sevColor } from "../theme";
import TaintGraph from "./TaintGraph";
import CutResult from "../components/CutResult";
import Coach from "../components/Coach";

type Cf = "noexec" | "taint" | "policy" | "outside";

// A tool result whose root names an untrusted source: the content an
// injection rides in, and what an operator's cut names.
function isTainted(s: ScrubberStep): boolean {
  if (s.kind !== "tool_result") return false;
  const root = s.payload.root as { sources?: unknown[] } | undefined;
  return Array.isArray(root?.sources) && root!.sources.length > 0;
}

const verdictOf = (s: ScrubberStep) => s.reevaluated_verdict ?? s.recorded_verdict;
const isRefused = (s: ScrubberStep) => s.kind === "tool_call" && verdictOf(s) === "deny";
const toolOf = (s: ScrubberStep) => (typeof s.payload.tool === "string" ? s.payload.tool : "");

function stepColor(s: ScrubberStep): string {
  if (isTainted(s)) return C.amber;
  return sevColor(verdictOf(s), s.kind);
}

// The words inside a timeline box: what happened at that step.
/** A step's box: the tool (or what happened) on the main line, and what the
 * step was — asked, answered, refused — on the small line above it. */
function stepChip(s: ScrubberStep): { what: string; name: string } {
  const tool = toolOf(s);
  if (s.kind === "tool_call") return { what: isRefused(s) ? "refused" : "asked", name: tool };
  if (s.kind === "tool_result") return { what: "answered", name: tool };
  const tags: Record<string, string> = {
    claim: "agent said",
    fault_injected: "broken on purpose",
    context_excision: "cut",
    fact: "note",
  };
  return { what: "", name: tags[s.kind] ?? s.kind.replace(/_/g, " ") };
}

/** What a call was asked to do, as the agent wrote it: the command, the query,
 * the address, the file — or every argument when none of those is there. */
function argsText(s: ScrubberStep): string {
  const args = s.payload.args;
  if (!args || typeof args !== "object") return "";
  const a = args as Record<string, unknown>;
  for (const k of ["command", "cmd", "query", "sql", "url", "file_path", "path"]) {
    if (typeof a[k] === "string" && a[k]) return a[k] as string;
  }
  const rest = Object.entries(a).filter(([, v]) => v !== "" && v != null);
  return rest.length ? rest.map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`).join("\n") : "";
}

function taintRef(s: ScrubberStep | undefined): string | null {
  if (!s) return null;
  if (typeof s.payload.value_ref === "string") return s.payload.value_ref;
  const argRefs = s.payload.arg_refs;
  if (argRefs && typeof argRefs === "object") {
    const first = Object.values(argRefs as Record<string, unknown>)[0];
    if (typeof first === "string") return first;
  }
  return null;
}

/** Where a value came from, as the steps recorded it: the answer that made it,
 * the call that asked for that answer, and what that call was given — back to
 * the first answer. Labels are tools, never value ids. */
function lineage(steps: ScrubberStep[], ref: string): { label: string; outside: boolean }[] {
  const out: { label: string; outside: boolean }[] = [];
  const seen = new Set<string>();
  let current: string | null = ref;
  while (current && !seen.has(current) && out.length < 8) {
    seen.add(current);
    const i = steps.findIndex((x) => x.kind === "tool_result" && x.payload.value_ref === current);
    if (i < 0) break;
    const res = steps[i];
    out.unshift({ label: `${toolOf(res)}'s answer`, outside: isTainted(res) });
    const call = steps.slice(0, i).reverse().find((x) => x.kind === "tool_call" && toolOf(x) === toolOf(res));
    const parents = call?.payload.arg_refs && typeof call.payload.arg_refs === "object"
      ? Object.values(call.payload.arg_refs as Record<string, unknown>).filter((v): v is string => typeof v === "string")
      : [];
    current = parents[0] ?? null;
  }
  return out;
}

function origin(steps: ScrubberStep[], s: ScrubberStep): string | null {
  const refs = s.payload.arg_refs && typeof s.payload.arg_refs === "object"
    ? Object.values(s.payload.arg_refs as Record<string, unknown>).filter((v): v is string => typeof v === "string")
    : [];
  for (const r of refs) {
    const chain = lineage(steps, r);
    const first = chain.find((c) => c.outside) ?? chain[0];
    if (first) return first.label;
  }
  return null;
}

/** Why a call was refused, in words — the kernel's own text stays under "details". */
function plainReason(steps: ScrubberStep[], s: ScrubberStep): string {
  const raw = String(s.deny_reason ?? s.payload.reason ?? "");
  const gate = s.gate ?? s.deny_category ?? "";
  if (/Refused by the axor rule/.test(raw)) return raw.split(" Refused by the axor rule")[0].replace(/^axor: /, "");
  if (/turned off from the Control Plane|paused from the Control Plane|stopped from the Control Plane/.test(raw)) {
    return raw.replace(/^axor: /, "");
  }
  if (gate === "taint_floor" || gate === "taint_enforcement" || /taint enforcement/.test(raw)) {
    const from = origin(steps, s);
    return `what it was going to use came from ${from ?? "text"} — text that came from outside`;
  }
  if (gate === "carrier" || gate === "carrier_gate") return "it would have followed an instruction that came from outside text";
  if (gate === "budget" || /budget/.test(raw)) return "the call budget was spent";
  if (gate === "ssrf" || gate === "ssrf_gate") return "it pointed at an internal address";
  if (gate === "value_policies" || gate === "value_policy") return "the value is not one of the allowed ones";
  if (/not in the capability table/.test(raw)) return "this tool is not on the allowed list";
  if (gate === "capability") return "this tool is not allowed here";
  return raw || "refused";
}

const FAULT_WORDS: Record<string, string> = {
  silent_fail: "made to return an error", corrupt_retrieval: "made to return corrupted results",
  instruction_injection: "made to return text with a hidden instruction",
  tool_substitution: "swapped for a different tool", timeout: "made to time out",
  empty_result: "made to answer with nothing",
};

/** One sentence for one step. */
function sentence(steps: ScrubberStep[], s: ScrubberStep): string {
  const tool = toolOf(s);
  if (s.kind === "tool_call") {
    return isRefused(s) ? `${tool} — refused: ${plainReason(steps, s)}.` : `The agent called ${tool}.`;
  }
  if (s.kind === "tool_result") {
    return isTainted(s)
      ? `${tool} answered — marked as outside text: anything built from it is watched.`
      : `${tool} answered.`;
  }
  if (s.kind === "claim") return `The agent said: “${String(s.payload.text ?? "").slice(0, 300)}”`;
  if (s.kind === "fault_injected") {
    return `${tool || "a tool"} was ${FAULT_WORDS[String(s.payload.mode)] ?? "broken"} — on purpose, for the test.`;
  }
  if (s.kind === "context_excision") return "An item was cut from the agent's context.";
  return s.kind.replace(/_/g, " ");
}

// `cursor` is a position in the step list; `seq` is a kernel event seq (what
// evidence anchors and regression rows name) and is resolved to its step once
// the trace loads.
// `node` disambiguates `seq` in a multi-node run, where every node counts from 0.
export default function ReplayTab({ runId: runIdProp, cursor: cursorProp, seq: seqProp, node: nodeProp }: {
  runId?: string; cursor?: string; seq?: string; node?: string;
}) {
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.listRuns });
  const policy = useApp((st) => st.toolPolicy);
  const [pickedRun, setPickedRun] = useState<string | null>(runIdProp ?? null);
  const runId = pickedRun ?? runIdProp ?? runs.data?.[0]?.run_id ?? null;

  const scrubber = useQuery({
    queryKey: ["scrubber", runId],
    queryFn: () => api.scrubber(runId!),
    enabled: !!runId,
  });

  const [cursor, setCursor] = useState(cursorProp ? Number(cursorProp) : 0);
  const [cf, setCf] = useState<Cf | null>(null);

  useEffect(() => {
    if (runIdProp) setPickedRun(runIdProp);
    if (cursorProp) setCursor(Number(cursorProp));
  }, [runIdProp, cursorProp]);

  // Pin state is per-run — clear the ✓ when the inspected run changes (including
  // deep-link navigation that does not go through pickRun).
  useEffect(() => {
    setPinned(null);
    setCf(null);
    setCursor(cursorProp ? Number(cursorProp) : 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  const counterfactual = useMutation({
    mutationFn: (config: Record<string, unknown>) => api.counterfactual(runId!, config),
  });

  // Keep the run as a test: an attack that must stay blocked, or normal work
  // that must keep passing — the only way to add the second side, since a
  // flow that passed has no incident to be kept from.
  const qc = useQueryClient();
  const [pinned, setPinned] = useState<string | null>(null);
  const pin = useMutation({
    mutationFn: (side: "must_block" | "must_pass") => api.pin(runId!, side, "kept from Replay"),
    onSuccess: (_r, side) => {
      setPinned(side);
      void qc.invalidateQueries({ queryKey: ["pins"] });
    },
  });

  const base: ScrubberPayload | undefined = scrubber.data;
  const active: ScrubberPayload | undefined =
    cf && counterfactual.data ? counterfactual.data : base;
  const steps = active?.steps ?? [];

  // Land a `seq` deep link on the step that carries it, once per link. Scrubber
  // steps carry no node id, but they fold the run's kernel events in stored
  // order, so the raw event at the same position names the node.
  const nodeEvents = useQuery({
    queryKey: ["run-events", runId],
    queryFn: () => api.runEvents(runId!),
    enabled: !!runId && !!seqProp && !!nodeProp,
  });
  const seqLanded = useRef<string | null>(null);
  useEffect(() => {
    if (!seqProp || !runId || !scrubber.data) return;
    if (nodeProp && nodeEvents.isPending) return;
    const key = `${runId}:${nodeProp ?? ""}:${seqProp}`;
    if (seqLanded.current === key) return;
    const want = Number(seqProp);
    const all = scrubber.data.steps;
    const kernel = (nodeEvents.data ?? []).filter((e) => e.schema_version);
    const aligned = kernel.length === all.length && kernel.every((e, i) => e.seq === all[i].seq);
    let i = nodeProp && aligned
      ? kernel.findIndex((e) => e.node_id === nodeProp && e.seq === want)
      : -1;
    if (i < 0) i = all.findIndex((x) => x.seq === want);
    if (i >= 0) setCursor(i);
    seqLanded.current = key;
  }, [seqProp, nodeProp, runId, scrubber.data, nodeEvents.isPending, nodeEvents.data]);
  const firstDiv = active?.first_divergence ?? null;
  // A what-if can only differ from something that was decided. A proxy-depth
  // trace records no verdicts (it watches; it does not decide), so nothing
  // could ever differ — say so instead of a false "nothing would change".
  const hasRecordedVerdicts = (base?.steps ?? []).some((x) => x.recorded_verdict != null);

  const toolsSeen = useMemo(() => {
    const seen: string[] = [];
    for (const x of base?.steps ?? []) {
      const t = x.payload.tool;
      if (typeof t === "string" && !seen.includes(t)) seen.push(t);
    }
    return seen;
  }, [base]);

  if (runs.isLoading) return <Center msg="loading runs…" />;
  if (!runId) return <Center msg="No run picked." />;

  const run = (runs.data ?? []).find((r) => r.run_id === runId);
  const incident = run?.evidence?.[0];
  const cur = steps[Math.min(cursor, Math.max(steps.length - 1, 0))];
  const curRef = taintRef(cur);
  const divIndex = firstDiv != null ? steps.findIndex((x) => x.seq === firstDiv) : -1;
  const divStep = divIndex >= 0 ? steps[divIndex] : undefined;
  const baseSteps = base?.steps ?? [];
  const outsideAt = baseSteps.findIndex(isTainted);
  const refusedAt = baseSteps.findIndex(isRefused);

  // The call this run should not have made: one that was let through while
  // what it was given came from outside text — the selected one, or else the
  // first in the run. Its rule: refuse anything built from outside text.
  const usedOutside = (x: ScrubberStep) => x.kind === "tool_call" && !isRefused(x)
    && lineage(baseSteps, taintRef(x) ?? "").some((f) => f.outside);
  const ruleStep = cur && usedOutside(cur) ? cur : baseSteps.find(usedOutside);
  const ruleTool = ruleStep ? toolOf(ruleStep) : "";
  const ruleKlass = ruleTool
    ? (policy[ruleTool]?.klass === "EXPORT" || policy[ruleTool]?.klass === "EXEC"
      ? policy[ruleTool].klass : guessKlass(ruleTool) === "EXPORT" ? "EXPORT" : "EXEC")
    : "EXEC";

  const toggleCf = (which: Cf) => {
    if (cf === which) {
      setCf(null);
      counterfactual.reset();
      return;
    }
    setCf(which);
    if (which === "noexec") {
      const shell = toolsSeen.find((t) => ["bash", "shell", "exec", "run_shell"].includes(t.toLowerCase()));
      counterfactual.mutate({ allowed_tools: toolsSeen.filter((t) => t !== shell) });
    } else if (which === "outside" && ruleTool) {
      counterfactual.mutate(ruleKlass === "EXPORT"
        ? { egress_sinks: [ruleTool] } : { imperative_sinks: [ruleTool] });
    } else if (which === "policy") {
      counterfactual.mutate(policyRules(policy));
    } else {
      counterfactual.mutate({ synthetic_taint_refs: curRef ? [curRef] : [] });
    }
  };
  const hasShell = toolsSeen.some((t) => ["bash", "shell", "exec", "run_shell"].includes(t.toLowerCase()));
  const canTaint = !!cur && cur.kind === "tool_result" && !isTainted(cur) && !!curRef;
  const policyTools = Object.keys(policy).length;

  const backToSearch = () => {
    let q: Record<string, string> = {};
    try { q = JSON.parse(sessionStorage.getItem(LAST_SEARCH) ?? "{}"); } catch { /* a fresh search */ }
    navigate("explain", q);
  };
  const link: React.CSSProperties = { background: "none", border: "none", padding: 0, cursor: "pointer",
    fontFamily: MONO, fontSize: 11.5, color: C.steel };

  return (
    <div style={{ maxWidth: 680, margin: "0 auto" }}>
      <Coach id="replay" title="Replay — a run, step by step">
        Each box is one step: a tool asked, a tool answering, the agent speaking. Green was let
        through, amber is text that came from outside, red was refused — with why. Below, ask whether other rules would have changed
        the run: the same recorded steps are decided again — no model is called — and the first step
        that would have gone differently is named.
      </Coach>
      <div data-tour="replay-picker" className="flex items-center gap-3 flex-wrap" style={{ marginBottom: 14, fontFamily: MONO, fontSize: 11.5 }}>
        <button onClick={backToSearch} data-testid="replay-back" style={link}>← find another step</button>
        {run && <span style={{ color: C.dim }}>{new Date(run.created_ts).toLocaleString(undefined, {
          weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" })}</span>}
      </div>

      <h1 data-testid="replay-title" style={{ fontSize: 21, fontWeight: 650, lineHeight: 1.35, margin: "0 0 4px" }}>
        {run?.node_id ?? runId}{" "}
        {incident
          ? <span style={{ color: C.red }}>{incidentWhat(incident.deviation)}</span>
          : <span style={{ color: C.mut }}>— a run, step by step</span>}
      </h1>
      {baseSteps.length > 0 && (
        <div data-testid="replay-summary" style={{ fontFamily: MONO, fontSize: 11.5, color: C.dim, marginBottom: 18 }}>
          {baseSteps.length} steps
          {outsideAt >= 0 && <> · <span style={{ color: C.amber }}>outside text at step {outsideAt + 1}</span></>}
          {refusedAt >= 0 && <> · <span style={{ color: C.red }}>refused at step {refusedAt + 1}</span></>}
          {incident && <> · <button onClick={() => navigate("evidence")} style={{ ...link, fontSize: 11.5 }}>the incident →</button></>}
        </div>
      )}
      {run?.node_id && <SessionMemory nodeId={run.node_id} />}

      {scrubber.isLoading ? (
        <Center msg="loading the run…" />
      ) : scrubber.isError ? (
        // e.g. a heartbeat-only live run: the backend answers 422 with the
        // reason — show it instead of a false "no steps recorded".
        <Center msg={(scrubber.error as Error).message} />
      ) : steps.length === 0 ? (
        <Center msg="No steps were recorded in this run." />
      ) : (
        <>
          <div className="flex gap-1 mb-1" data-testid="replay-steps">
            {steps.map((x, i) => (
              <div key={x.seq} onClick={() => setCursor(i)} title={`${i + 1}. ${sentence(steps, x)}`}
                data-testid={`step-${i + 1}`}
                style={{
                  flex: 1, minWidth: 0, height: 38, cursor: "pointer", borderRadius: 4,
                  background: i === cursor ? "rgba(127,168,204,0.08)" : C.panel,
                  opacity: x.hypothetical ? 0.35 : 1,
                  border: `1px solid ${i === cursor ? C.steel : divIndex === i ? C.red : C.line}`,
                  borderBottom: `3px solid ${stepColor(x)}`,
                  display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", padding: "0 4px",
                }}>
                <span style={{ fontFamily: MONO, fontSize: 8.5, color: isRefused(x) ? C.red : isTainted(x) ? C.amber : C.dim,
                               whiteSpace: "nowrap", overflow: "hidden", maxWidth: "100%" }}>
                  {i + 1}{stepChip(x).what ? ` · ${stepChip(x).what}` : ""}
                </span>
                <span style={{ fontFamily: MONO, fontSize: 9.5, color: i === cursor ? C.text : isRefused(x) ? C.red : C.mut,
                               whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", maxWidth: "100%" }}>
                  {stepChip(x).name}
                </span>
              </div>
            ))}
          </div>
          <div className="flex gap-3 mb-2" style={{ fontFamily: MONO, fontSize: 10, color: C.dim, marginTop: 8 }}>
            <span><span style={{ color: C.green }}>━</span> let through</span>
            <span><span style={{ color: C.amber }}>━</span> text from outside</span>
            <span><span style={{ color: C.red }}>━</span> refused</span>
          </div>
          <input type="range" min={0} max={steps.length - 1} value={Math.min(cursor, steps.length - 1)}
            onChange={(e) => setCursor(+e.target.value)} aria-label="step" className="w-full" style={{ accentColor: C.steel }} />

          {cur && (
            <div data-testid="replay-step" className="mt-3 p-4"
              style={{ background: C.panel, border: `1px solid ${isRefused(cur) ? C.red : C.line}`, borderRadius: 8 }}>
              <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, marginBottom: 4 }}>
                STEP {Math.min(cursor, steps.length - 1) + 1} OF {steps.length}{cur.hypothetical ? " · would not have happened" : ""}
              </div>
              <div style={{ fontSize: 14.5, color: isRefused(cur) ? C.red : C.text, lineHeight: 1.5 }}>
                {sentence(steps, cur)}
              </div>
              {typeof cur.payload.preview === "string" && cur.payload.preview && (
                <div style={{ fontFamily: MONO, fontSize: 11, color: C.text, marginTop: 8, background: C.panel2,
                              borderRadius: 4, padding: "6px 8px", whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
                  {cur.payload.preview}
                </div>
              )}
              {cur.kind === "tool_call" && argsText(cur) && (
                <div data-testid="replay-args" style={{ fontFamily: MONO, fontSize: 11.5, color: C.text, marginTop: 8, background: C.panel2,
                              borderRadius: 4, padding: "6px 8px", whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
                  {argsText(cur)}
                </div>
              )}
              {cur.kind === "tool_call" && (() => {
                const from = lineage(steps, taintRef(cur) ?? "");
                return from.length > 0 ? (
                  <div data-testid="replay-origin" style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, marginTop: 8 }}>
                    what it was given came from:{" "}
                    {from.map((f, i) => (
                      <span key={i}>
                        {i > 0 && " → "}
                        <span style={{ color: f.outside ? C.amber : C.text }}>{f.label}{f.outside ? " (outside text)" : ""}</span>
                      </span>
                    ))}
                  </div>
                ) : null;
              })()}
              {isTainted(cur) && typeof cur.payload.value_ref === "string" && (
                <CutResult runId={runId} valueRef={cur.payload.value_ref}
                  excised={cur.state.excised_refs.includes(cur.payload.value_ref)} />
              )}
              <Fold label="details">
                <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, lineHeight: 1.7 }}>
                  {cur.kind} · seq {cur.seq} · {cur.state.budget_spent_calls} calls so far ·{" "}
                  {cur.state.tainted_refs.length} values from outside so far · level {cur.state.level}
                  {(cur.deny_reason || cur.payload.reason) ? <div>kernel: {String(cur.deny_reason ?? cur.payload.reason)}</div> : null}
                </div>
                {curRef && <TaintGraph key={`${runId}:${curRef}`} runId={runId} focus={curRef} />}
              </Fold>
            </div>
          )}

          <div data-testid="replay-whatif" className="mt-4 p-4" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
            <div style={{ fontSize: 15, fontWeight: 600, color: C.text, marginBottom: 2 }}>Would other rules have changed this run?</div>
            <div style={{ fontSize: 12.5, color: C.mut, marginBottom: 10 }}>
              Decide the same recorded steps again under other rules — no model is called, same answer every time.
            </div>
            <div className="flex gap-2 flex-wrap mb-3">
              {([
                ["outside", `${ruleTool} refuses anything built from outside text`, !!ruleTool],
                ["noexec", "no shell commands at all", hasShell],
                ["taint", "this answer treated as outside text", canTaint],
                ["policy", `the rules from Policy (${policyTools} tools)`, policyTools > 0],
              ] as const).filter(([, , ok]) => ok).map(([id, label]) => (
                <button key={id} onClick={() => toggleCf(id)} data-testid={`whatif-${id}`}
                  style={btn({ background: cf === id ? "rgba(127,168,204,0.12)" : "none",
                    border: `1px solid ${cf === id ? C.steel : C.line}`, color: cf === id ? C.steel : C.mut, fontSize: 11.5 })}>
                  {cf === id ? "✓ " : "try: "}{label}
                </button>
              ))}
            </div>
            <div data-testid="whatif-answer" style={{ fontSize: 13.5, lineHeight: 1.6 }}>
              {cf === null ? (
                <span style={{ color: C.mut }}>
                  {!hasShell && !canTaint && policyTools === 0
                    ? "Select a tool's answer above to try it as outside text."
                    : "Pick one — the answer shows here."}
                </span>
              ) : counterfactual.isPending ? (
                <span style={{ color: C.mut }}>deciding again…</span>
              ) : counterfactual.isError ? (
                <span style={{ color: C.red }}>{(counterfactual.error as Error).message}</span>
              ) : firstDiv == null && !hasRecordedVerdicts ? (
                <span style={{ color: C.amber }}>
                  This run was recorded by the proxy, which watches without deciding — there is nothing to
                  decide differently. Agents wrapped in code record their decisions.
                </span>
              ) : firstDiv == null || !divStep ? (
                <span style={{ color: C.mut }}>Nothing would have gone differently.</span>
              ) : (
                <>
                  It would have gone differently at <span style={{ color: C.red }}>step {divIndex + 1}</span>
                  {toolOf(divStep) ? <> ({toolOf(divStep)})</> : null}:{" "}
                  {isRefused(divStep) ? <>refused — {plainReason(steps, divStep)}.</> : <>let through.</>}
                  <div style={{ fontFamily: MONO, color: C.dim, fontSize: 11, marginTop: 4 }}>
                    {divIndex > 0 ? `steps 1–${divIndex} go as before · ` : ""}the steps after it would not have happened — shown faded
                  </div>
                </>
              )}
            </div>
            {cf === "outside" && divStep && isRefused(divStep) && toolOf(divStep) === ruleTool && run && (
              <MakeRule runId={runId} node={nodeProp ?? run.node_id} seq={ruleStep!.seq} tool={ruleTool} klass={ruleKlass}
                tools={toolsSeen.map((t) => ({
                  name: t,
                  outside: baseSteps.some((x) => x.kind === "tool_result" && toolOf(x) === t && isTainted(x)),
                }))}
                otherRuns={(runs.data ?? []).filter((r) => r.node_id === run.node_id && r.run_id !== runId
                  && (r.evidence ?? []).length === 0).map((r) => r.run_id)} />
            )}
          </div>

          <div data-testid="replay-keep" className="mt-4 flex items-center gap-2 flex-wrap" style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut }}>
            keep this run as a test:
            {(["must_block", "must_pass"] as const).map((side) => (
              <button key={side} onClick={() => pin.mutate(side)} disabled={pin.isPending}
                style={btn({ color: pinned === side ? C.green : C.text, fontSize: 11, padding: "4px 10px" })}>
                {pinned === side ? "✓ " : ""}{side === "must_block" ? "an attack — must stay blocked" : "normal work — must keep passing"}
              </button>
            ))}
            {pin.isError && <span style={{ color: C.red }}>{(pin.error as Error).message}</span>}
            {pinned && <button onClick={() => navigate("prevent")} style={link}>check it in Safe to ship? →</button>}
          </div>
        </>
      )}
    </div>
  );
}

const KLASS_WORDS: Record<string, string> = {
  READ: "reads", WRITE: "changes files", EXPORT: "sends out", EXEC: "runs code",
};

/** Turn the what-if into the rule: the tool's kind goes into Policy (refusing
 * anything built from outside text follows from it), the run's other tools are
 * added as they look, the run is kept as an attack that must stay blocked, and
 * the agent's other runs as normal work that must keep passing — then Safe to
 * ship? checks exactly that. */
function MakeRule({ runId, node, seq, tool, klass, tools: inRun, otherRuns }: {
  runId: string; node: string; seq: number; tool: string; klass: "EXEC" | "EXPORT";
  tools: { name: string; outside: boolean }[]; otherRuns: string[];
}) {
  const policy = useApp((st) => st.toolPolicy);
  const setToolClass = useApp((st) => st.setToolClass);
  const qc = useQueryClient();
  const [keepNormal, setKeepNormal] = useState(true);
  // every tool the agent has called, not only in this run: a tool missing from
  // Policy is refused, and its normal work uses tools this run never did
  const seen = useQuery({
    queryKey: ["agent-tools", node],
    queryFn: () => api.searchSteps({ agent: node }),
  });
  const tools = useMemo(() => {
    const all = toolsFromSteps((seen.data?.steps ?? []).filter((x) => x.node_id === node));
    for (const t of inRun) all[t.name] = { klass: all[t.name]?.klass ?? guessKlass(t.name), outside: t.outside || !!all[t.name]?.outside };
    return Object.entries(all).map(([name, c]) => ({ name, outside: c.outside }));
  }, [seen.data, inRun, node]);
  const added = tools.filter((t) => t.name !== tool && !policy[t.name]);
  const apply = useMutation({
    mutationFn: async () => {
      const source = `seen in ${node}'s runs`;
      for (const t of added) setToolClass(t.name, { klass: guessKlass(t.name), outside: t.outside, source });
      setToolClass(tool, { klass, outside: tools.find((t) => t.name === tool)?.outside ?? false,
                           source: policy[tool]?.source ?? source });
      await api.pin(runId, "must_block", `${node}: ${tool} built from outside text`,
        [{ node_id: node, seq }]);
      // the incident this call was in the feed as, settled
      await api.setIncidentStatus(`step:${runId}:${node}:${seq}`, "rule_made").catch(() => undefined);
      if (keepNormal) {
        for (const r of otherRuns) await api.pin(r, "must_pass", `normal work of ${node}`);
      }
    },
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["pins"] });
      navigate("prevent", { rules: "policy" });
    },
  });
  return (
    <div data-testid="make-rule" className="mt-4 p-3" style={{ background: C.panel2, border: `1px solid ${C.steel}`, borderRadius: 6 }}>
      <div style={{ fontSize: 13.5, fontWeight: 600, color: C.text, marginBottom: 6 }}>Make it a rule</div>
      <ul style={{ fontSize: 12.5, color: C.mut, lineHeight: 1.7, margin: 0, paddingLeft: 18 }}>
        <li>
          In Policy, <span style={{ fontFamily: MONO, color: C.text }}>{tool}</span> {KLASS_WORDS[klass]} — so{" "}
          {klass === "EXEC" ? "a command" : "anything it sends"} built from outside text is refused, whoever asked.
        </li>
        {added.length > 0 && (
          <li>
            {node}'s other tools go into Policy as they look:{" "}
            {added.map((t, i) => (
              <span key={t.name}>{i > 0 && ", "}<span style={{ fontFamily: MONO, color: C.text }}>{t.name}</span>{" "}
                {KLASS_WORDS[guessKlass(t.name)]}{t.outside ? " · brings outside text" : ""}</span>
            ))}. A tool not in Policy is refused.
          </li>
        )}
        <li>This run is kept as an attack: its {tool} call must be refused.</li>
        {otherRuns.length > 0 && (
          <li>
            <label style={{ cursor: "pointer" }}>
              <input type="checkbox" checked={keepNormal} onChange={(e) => setKeepNormal(e.target.checked)}
                style={{ marginRight: 6, verticalAlign: "middle" }} />
              {node}'s other {otherRuns.length === 1 ? "run is" : `${otherRuns.length} runs are`} kept as normal work
              that must keep passing — so a rule that is too strict shows up.
            </label>
          </li>
        )}
      </ul>
      <div className="flex items-center gap-3 mt-3">
        <button onClick={() => apply.mutate()} disabled={apply.isPending || seen.isPending} data-testid="make-rule-apply"
          style={btn({ color: C.text, borderColor: C.steel, fontSize: 12, padding: "6px 14px" })}>
          {apply.isPending ? "…" : "Make it a rule and check it →"}
        </button>
        <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>opens Safe to ship? with the rules from Policy</span>
        {apply.isError && <span style={{ color: C.red, fontSize: 12 }}>{(apply.error as Error).message}</span>}
      </div>
    </div>
  );
}

/** The team's memory this session's agent was given at its start, if any. */
function SessionMemory({ nodeId }: { nodeId: string }) {
  const q = useQuery({ queryKey: ["memory-of", nodeId], queryFn: () => api.memoryOfSession(nodeId), retry: false });
  const [open, setOpen] = useState(false);
  const given = (q.data?.given ?? []).filter((g) => g.items.length);
  if (!given.length) return null;
  const last = given[given.length - 1];
  return (
    <div data-testid="replay-memory" style={{ fontFamily: MONO, fontSize: 11, color: C.dim, margin: "-10px 0 16px" }}>
      given the team memory v {last.version} ({last.items.length} fact{last.items.length === 1 ? "" : "s"}) ·{" "}
      <button onClick={() => setOpen(!open)} style={{ background: "none", border: "none", padding: 0, cursor: "pointer",
        fontFamily: MONO, fontSize: 11, color: C.steel }}>{open ? "hide" : "what it said"}</button>
      {open && (
        <ul style={{ margin: "6px 0 0 16px", color: C.mut, lineHeight: 1.6 }}>
          {last.items.map((i) => <li key={i.id}>{i.text}</li>)}
        </ul>
      )}
    </div>
  );
}

function Fold({ label, children }: { label: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-2">
      <button onClick={() => setOpen(!open)} style={{ background: "none", border: "none", padding: 0, cursor: "pointer",
        fontFamily: MONO, fontSize: 10.5, color: C.dim }}>
        {open ? "▾" : "▸"} {label}
      </button>
      {open && <div className="mt-2">{children}</div>}
    </div>
  );
}

function Center({ msg }: { msg: string }) {
  return (
    <div style={{ maxWidth: 640, margin: "0 auto", fontFamily: MONO, fontSize: 12.5, color: C.mut }}>
      {msg}
    </div>
  );
}

