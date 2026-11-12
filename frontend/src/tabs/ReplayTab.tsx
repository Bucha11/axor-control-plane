// Replay: a timeline you can question (main-tabs mockup, ReplayTab).
// Wired to /v1/replay/{run_id}; counterfactuals re-evaluate gates over the
// recorded trace — no model call, fully deterministic.
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ScrubberPayload, ScrubberStep } from "../api";
import { navigate } from "../router";
import { C, MONO, btn, sevColor } from "../theme";
import TaintGraph from "./TaintGraph";
import Coach from "../components/Coach";
import Tooltip from "../components/Tooltip";

// A one-click seed of adapter-fidelity runs (recorded verdicts + value
// provenance) — the trace depth the proxy can't produce, so the counterfactual,
// the taint graph and two-sided regression can be shown with real data.
function useSeedExample(onDone?: (blockRunId: string) => void) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.seedAdapterRuns(),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["runs"] });
      onDone?.("ex_block");
    },
  });
}

type Cf = "noexec" | "taint";

// A tool result whose root names an untrusted source: the content an
// injection rides in, and what an operator's cut names.
function isTainted(s: ScrubberStep): boolean {
  if (s.kind !== "tool_result") return false;
  const root = s.payload.root as { sources?: unknown[] } | undefined;
  return Array.isArray(root?.sources) && root!.sources.length > 0;
}

function stepColor(s: ScrubberStep): string {
  if (isTainted(s)) return C.amber;
  return sevColor(s.reevaluated_verdict ?? s.recorded_verdict, s.kind);
}

function stepLabel(s: ScrubberStep): string {
  const tool = typeof s.payload.tool === "string" ? ` ${s.payload.tool}` : "";
  if (s.kind === "tool_result") {
    const sources = ((s.payload.root as { sources?: string[] } | undefined)?.sources ?? []).join(", ");
    return `result of${tool}${sources ? ` — untrusted (${sources})` : ""}`;
  }
  const deny = s.deny_reason ? ` — ${s.deny_reason}` : "";
  return `${s.kind}${tool}${deny}`;
}

// The short label inside a timeline box: the tool name when there is one, else a
// compact tag for the non-tool event kinds (claim, fault, excision, …).
function stepChip(s: ScrubberStep): string {
  if (typeof s.payload.tool === "string" && s.payload.tool) {
    // a call and its result are two steps of the same tool: say which
    if (s.kind === "tool_call") return `→ ${s.payload.tool}`;
    if (s.kind === "tool_result") return `← ${s.payload.tool}`;
    return s.payload.tool;
  }
  const tags: Record<string, string> = {
    claim: "claim",
    fault_injected: "⚡fault",
    context_excision: "excision",
    fact: "fact",
    tool_result: "result",
  };
  return tags[s.kind] ?? s.kind;
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

// `cursor` is a position in the step list; `seq` is a kernel event seq (what
// evidence anchors and regression rows name) and is resolved to its step once
// the trace loads.
// `node` disambiguates `seq` in a multi-node run, where every node counts from 0.
export default function ReplayTab({ runId: runIdProp, cursor: cursorProp, seq: seqProp, node: nodeProp }: {
  runId?: string; cursor?: string; seq?: string; node?: string;
}) {
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.listRuns });
  const [pickedRun, setPickedRun] = useState<string | null>(runIdProp ?? null);
  const runId = pickedRun ?? runIdProp ?? runs.data?.[0]?.run_id ?? null;

  const scrubber = useQuery({
    queryKey: ["scrubber", runId],
    queryFn: () => api.scrubber(runId!),
    enabled: !!runId,
  });

  const [cursor, setCursor] = useState(cursorProp ? Number(cursorProp) : 0);
  const [fork, setFork] = useState(false);
  const [cf, setCf] = useState<Cf | null>(null);

  useEffect(() => {
    if (runIdProp) setPickedRun(runIdProp);
    if (cursorProp) setCursor(Number(cursorProp));
  }, [runIdProp, cursorProp]);

  // Pin state is per-run — clear the ✓ when the inspected run changes (including
  // deep-link navigation that does not go through pickRun).
  useEffect(() => {
    setPinned(null);
  }, [runId]);

  const counterfactual = useMutation({
    mutationFn: (config: Record<string, unknown>) =>
      api.counterfactual(runId!, config),
  });

  // Pin the run under inspection to either corpus side. must_block auto-pins on
  // evidence upload, but a legitimate flow that PASSED has no evidence — this is
  // the only way to add the must_pass side the corpus needs to be two-sided.
  const qc = useQueryClient();
  const [pinned, setPinned] = useState<string | null>(null);
  const pin = useMutation({
    mutationFn: (side: "must_block" | "must_pass") =>
      api.pin(runId!, side, "manual"),
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
    const steps = scrubber.data.steps;
    const kernel = (nodeEvents.data ?? []).filter((e) => e.schema_version);
    const aligned = kernel.length === steps.length
      && kernel.every((e, i) => e.seq === steps[i].seq);
    let i = nodeProp && aligned
      ? kernel.findIndex((e) => e.node_id === nodeProp && e.seq === want)
      : -1;
    if (i < 0) i = steps.findIndex((s) => s.seq === want);
    if (i >= 0) setCursor(i);
    seqLanded.current = key;
  }, [seqProp, nodeProp, runId, scrubber.data, nodeEvents.isPending, nodeEvents.data]);
  const firstDiv = active?.first_divergence ?? null;
  // A counterfactual can only DIVERGE from something that was recorded. A
  // proxy-depth trace records no gate verdicts (it observes; it does not gate),
  // so first_divergence can never fire — reporting "behaves identically" there
  // would be a false reassurance. Detect the fidelity and say so honestly.
  const hasRecordedVerdicts = (base?.steps ?? []).some(
    (s) => s.recorded_verdict != null,
  );

  const toolsSeen = useMemo(() => {
    const seen: string[] = [];
    for (const s of base?.steps ?? []) {
      const t = s.payload.tool;
      if (typeof t === "string" && !seen.includes(t)) seen.push(t);
    }
    return seen;
  }, [base]);

  if (runs.isLoading) {
    return <Center msg="loading runs…" />;
  }
  if (!runId) {
    return (
      <div style={{ maxWidth: 640, margin: "0 auto" }}>
        <div data-tour="replay-picker">
          <div style={{ fontFamily: MONO, fontSize: 12.5, color: C.mut, marginBottom: 12 }}>
            No runs yet. Catch one first, from Caught lies —
          </div>
          <SeedButton label="or load an example adapter run →" />
        </div>
      </div>
    );
  }

  const cur = steps[Math.min(cursor, Math.max(steps.length - 1, 0))];
  const cursorTool = typeof cur?.payload.tool === "string" ? cur.payload.tool : null;
  const curRef = taintRef(cur);
  const divStep = firstDiv != null ? steps.find((s) => s.seq === firstDiv) : undefined;

  const pickRun = (id: string) => {
    if (id !== runId) navigate(`explain/${id}`);
    setPickedRun(id);
    setCursor(0);
    setFork(false);
    setCf(null);
    counterfactual.reset();
  };

  const toggleCf = (which: Cf) => {
    if (cf === which) {
      setCf(null);
      counterfactual.reset();
      return;
    }
    setCf(which);
    if (which === "noexec") {
      const drop = toolsSeen.includes("bash") ? "bash" : cursorTool;
      counterfactual.mutate({
        allowed_tools: toolsSeen.filter((t) => t !== drop),
      });
    } else {
      const ref = taintRef(cur);
      counterfactual.mutate({ synthetic_taint_refs: ref ? [ref] : [] });
    }
  };

  return (
    <div style={{ maxWidth: 640, margin: "0 auto" }}>
      <Coach id="replay" title="Replay — question a run, moment by moment">
        Scrub the timeline; each box is one step, coloured by verdict.{" "}
        <span style={{ color: C.text }}>What if…</span> forks a counterfactual —
        it re-runs the recorded trace under an edited policy (no model call) and
        shows the first step that would change. The provenance graph traces where a
        value came from. Pin a run to build the regression corpus.
      </Coach>
      <div data-tour="replay-picker" className="flex items-center gap-3 mb-3">
        <select
          value={runId}
          onChange={(e) => pickRun(e.target.value)}
          style={{
            background: C.panel2, border: `1px solid ${C.line}`, borderRadius: 5,
            color: C.text, fontFamily: MONO, fontSize: 11, padding: "5px 8px",
          }}
        >
          {(runs.data ?? []).map((r) => (
            <option key={r.run_id} value={r.run_id}>
              {r.run_id} · {r.scenario}
            </option>
          ))}
        </select>
        <SeedButton label="load example adapter run" compact />
      </div>

      <div className="flex items-center gap-2 mb-3">
        <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>pin to corpus:</span>
        {(["must_pass", "must_block"] as const).map((side) => (
          <button
            key={side}
            onClick={() => pin.mutate(side)}
            disabled={pin.isPending}
            title={side === "must_pass"
              ? "add this run as a legitimate flow that must keep passing"
              : "add this run as an attack that must stay blocked"}
            style={btn({
              color: pinned === side ? C.green : C.mut, borderColor: C.line,
              fontSize: 10.5, padding: "3px 9px",
            })}
          >
            {pinned === side ? "✓ " : ""}{side.replace("_", "-")}
          </button>
        ))}
        {pin.isError && (
          <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.red }}>
            pin failed: {(pin.error as Error).message}
          </span>
        )}
        {pinned && (
          <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>
            pinned → <span style={{ color: C.steel, cursor: "pointer" }} onClick={() => navigate("prevent")}>check it in Safe to ship? →</span>
          </span>
        )}
      </div>

      <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 20px" }}>
        {runId}, moment by moment.
      </h1>

      {scrubber.isLoading ? (
        <Center msg="loading trace…" />
      ) : scrubber.isError ? (
        // e.g. a heartbeat-only live run: the backend answers 422 with the
        // reason — show it instead of a false "no steps recorded".
        <Center msg={(scrubber.error as Error).message} />
      ) : steps.length === 0 ? (
        <Center msg="No steps recorded for this run." />
      ) : (
        <>
          <div className="flex gap-1 mb-2">
            {steps.map((s, i) => (
              <div
                key={s.seq}
                onClick={() => setCursor(i)}
                title={`${s.seq}. ${stepLabel(s)}`}
                style={{
                  flex: 1, minWidth: 0, height: 34, cursor: "pointer", borderRadius: 4,
                  background: i === cursor ? "rgba(127,168,204,0.08)" : C.panel,
                  opacity: s.hypothetical ? 0.35 : 1,
                  border: `1px solid ${
                    i === cursor ? C.steel : firstDiv != null && s.seq === firstDiv ? C.red : C.line
                  }`,
                  borderBottom: `3px solid ${stepColor(s)}`,
                  display: "flex", alignItems: "center", justifyContent: "center",
                  padding: "0 4px",
                }}
              >
                <span style={{
                  fontFamily: MONO, fontSize: 9.5,
                  color: i === cursor ? C.text : C.mut,
                  whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                }}>
                  {stepChip(s)}
                </span>
              </div>
            ))}
          </div>
          <input
            type="range"
            min={0}
            max={steps.length - 1}
            value={Math.min(cursor, steps.length - 1)}
            onChange={(e) => setCursor(+e.target.value)}
            className="w-full"
            style={{ accentColor: C.steel }}
          />

          {cur && (
            <div className="mt-3 p-4" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
              <div style={{ fontFamily: MONO, fontSize: 13, color: C.text }}>
                {stepLabel(cur)}
              </div>
              <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginTop: 6 }}>
                level {cur.state.level} · {cur.state.tainted_refs.length} tainted so far ·{" "}
                {cur.state.budget_spent_calls} calls spent
                {typeof cur.state.budget_spent_cost === "number" &&
                  cur.state.budget_spent_cost !== cur.state.budget_spent_calls &&
                  ` · cost ${cur.state.budget_spent_cost}`}
              </div>
              {typeof cur.payload.preview === "string" && cur.payload.preview && (
                <div style={{ fontFamily: MONO, fontSize: 11, color: C.text, marginTop: 8,
                              background: C.panel2, borderRadius: 4, padding: "6px 8px",
                              whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
                  {cur.payload.preview}
                </div>
              )}
              {isTainted(cur) && typeof cur.payload.value_ref === "string" && (
                <CutResult runId={runId} valueRef={cur.payload.value_ref}
                  excised={cur.state.excised_refs.includes(cur.payload.value_ref)} />
              )}
            </div>
          )}

          {curRef && <TaintGraph key={`${runId}:${curRef}`} runId={runId} focus={curRef} />}

          {!fork ? (
            <Tooltip content="Fork a counterfactual from this run: change one policy and see how the recorded trace would re-gate — deterministic, no model call.">
              <button
                onClick={() => setFork(true)}
                className="mt-4"
                style={btn({ color: C.steel, fontSize: 12, padding: "8px 14px" })}
              >
                What if… (fork here)
              </button>
            </Tooltip>
          ) : (
            <div className="mt-4 p-4" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
              <div className="flex gap-2 mb-3">
                {([
                  ["no exec capability", "noexec"],
                  ["this value arrives tainted", "taint"],
                ] as const).map(([label, id]) => (
                  <button
                    key={id}
                    onClick={() => toggleCf(id)}
                    style={btn({
                      background: cf === id ? "rgba(127,168,204,0.12)" : "none",
                      border: `1px solid ${cf === id ? C.steel : C.line}`,
                      color: cf === id ? C.steel : C.mut,
                    })}
                  >
                    {label}
                  </button>
                ))}
              </div>
              {cf === null ? (
                <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut }}>
                  Pick a change. Gates re-evaluate over the recorded trace — no model call, fully deterministic.
                </div>
              ) : counterfactual.isPending ? (
                <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut }}>re-evaluating…</div>
              ) : counterfactual.isError ? (
                <div style={{ fontFamily: MONO, fontSize: 11, color: C.red }}>
                  {(counterfactual.error as Error).message}
                </div>
              ) : firstDiv == null && !hasRecordedVerdicts ? (
                <div style={{ fontFamily: MONO, fontSize: 12, color: C.amber }}>
                  This trace carries no recorded gate verdicts (proxy-depth — the proxy observes, it does not gate),
                  so there is nothing to diverge from.
                  <div style={{ color: C.dim, fontSize: 11, marginTop: 4 }}>
                    Counterfactual divergence needs an adapter-depth trace (recorded verdicts + value provenance).
                  </div>
                </div>
              ) : firstDiv == null ? (
                <div style={{ fontFamily: MONO, fontSize: 12, color: C.mut }}>
                  No divergence — this trace behaves identically under the edited config.
                </div>
              ) : (
                <div style={{ fontFamily: MONO, fontSize: 12, color: C.text }}>
                  First divergence at <span style={{ color: C.red }}>step {firstDiv}</span>:{" "}
                  {divStep?.deny_category ?? "diverged"} — {divStep?.deny_reason ?? "gate outcome changed"}.
                  <div style={{ color: C.dim, fontSize: 11, marginTop: 4 }}>
                    Steps after {firstDiv} are hypothetical and excluded from scores.
                  </div>
                </div>
              )}
            </div>
          )}
        </>
      )}
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

function SeedButton({ label, compact }: { label: string; compact?: boolean }) {
  const seed = useSeedExample((blockRunId) => navigate(`explain/${blockRunId}`));
  return (
    <button
      onClick={() => seed.mutate()}
      disabled={seed.isPending}
      title="ingests two adapter-fidelity runs so counterfactual, taint graph and two-sided regression work with real data"
      style={btn({
        color: C.steel, borderColor: C.line,
        fontSize: compact ? 10.5 : 12, padding: compact ? "4px 9px" : "7px 14px",
      })}
    >
      {seed.isPending ? "seeding…" : label}
    </button>
  );
}

// Cut this tool result from the agent's context — the same signed command the
// Health panel's self-heal sends, aimed at the result you are looking at. The
// node applies it at its next boundary, once its conversation carries the
// result (an adapter rebuilds per run; see axor-langchain / axor-claude).
function CutResult({ runId, valueRef, excised }: { runId: string; valueRef: string; excised: boolean }) {
  const qc = useQueryClient();
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.listRuns });
  const nodeId = (runs.data ?? []).find((r) => r.run_id === runId)?.node_id;
  const nodes = useQuery({ queryKey: ["nodes"], queryFn: api.nodes, enabled: !!nodeId });
  const node = (nodes.data ?? []).find((n) => n.node_id === nodeId);
  const pending = node?.desired?.state.pending_excision as { target_refs?: string[] } | undefined;
  const cut = useMutation({
    mutationFn: (reason: string) => api.command(nodeId!, (node?.desired?.version ?? 0) + 1, {
      pending_excision: { id: `exc_${Date.now()}`, target_refs: [valueRef], reason, operator: "op_ui" },
    }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["nodes"] }),
  });
  if (!nodeId || !node) return null;   // not a live node's run: nothing to command
  const queued = pending?.target_refs?.includes(valueRef);
  return (
    <div className="mt-3 flex items-center gap-2 flex-wrap" style={{ fontFamily: MONO, fontSize: 11 }}>
      {excised ? (
        <span style={{ color: C.green }}>cut — this result no longer reaches the model</span>
      ) : queued || cut.isSuccess ? (
        <span style={{ color: C.amber }}>cut sent to {nodeId} — applied when its conversation carries {valueRef}</span>
      ) : (
        <Tooltip content="Redact this tool result from the agent's context (and, for a file it read, the cached copy). A signed operator command; the reason is recorded.">
          <button
            onClick={() => {
              const reason = window.prompt(`Cut ${valueRef} from ${nodeId}'s context — reason (recorded):`);
              if (reason && reason.trim()) cut.mutate(reason.trim());
            }}
            disabled={cut.isPending}
            style={btn({ color: C.amber, borderColor: C.amber, fontSize: 11, padding: "4px 10px" })}>
            cut this result from {nodeId}
          </button>
        </Tooltip>
      )}
      {cut.isError && <span style={{ color: C.red }}>{(cut.error as Error).message}</span>}
    </div>
  );
}
