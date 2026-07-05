// Replay: a timeline you can question (main-tabs mockup, ReplayTab).
// Wired to /v1/replay/{run_id}; counterfactuals re-evaluate gates over the
// recorded trace — no model call, fully deterministic.
import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, ScrubberPayload, ScrubberStep } from "../api";
import { C, MONO, btn, sevColor } from "../theme";

type Cf = "noexec" | "taint";

function stepColor(s: ScrubberStep): string {
  return sevColor(s.reevaluated_verdict ?? s.recorded_verdict, s.kind);
}

function stepLabel(s: ScrubberStep): string {
  const tool = typeof s.payload.tool === "string" ? ` ${s.payload.tool}` : "";
  const deny = s.deny_reason ? ` — ${s.deny_reason}` : "";
  return `${s.kind}${tool}${deny}`;
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

export default function ReplayTab() {
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.listRuns });
  const [pickedRun, setPickedRun] = useState<string | null>(null);
  const runId = pickedRun ?? runs.data?.[0]?.run_id ?? null;

  const scrubber = useQuery({
    queryKey: ["scrubber", runId],
    queryFn: () => api.scrubber(runId!),
    enabled: !!runId,
  });

  const [cursor, setCursor] = useState(0);
  const [fork, setFork] = useState(false);
  const [cf, setCf] = useState<Cf | null>(null);

  const counterfactual = useMutation({
    mutationFn: (config: Record<string, unknown>) =>
      api.counterfactual(runId!, config),
  });

  const base: ScrubberPayload | undefined = scrubber.data;
  const active: ScrubberPayload | undefined =
    cf && counterfactual.data ? counterfactual.data : base;
  const steps = active?.steps ?? [];
  const firstDiv = active?.first_divergence ?? null;

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
    return <Center msg="No runs yet. Run an experiment from the Eval tab first." />;
  }

  const cur = steps[Math.min(cursor, Math.max(steps.length - 1, 0))];
  const cursorTool = typeof cur?.payload.tool === "string" ? cur.payload.tool : null;
  const divStep = firstDiv != null ? steps.find((s) => s.seq === firstDiv) : undefined;

  const pickRun = (id: string) => {
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
      <div className="flex items-center gap-3 mb-3">
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
      </div>

      <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 20px" }}>
        {runId}, moment by moment.
      </h1>

      {scrubber.isLoading ? (
        <Center msg="loading trace…" />
      ) : steps.length === 0 ? (
        <Center msg="No steps recorded for this run." />
      ) : (
        <>
          <div className="flex gap-1 mb-2">
            {steps.map((s, i) => (
              <div
                key={s.seq}
                onClick={() => setCursor(i)}
                style={{
                  flex: 1, height: 32, cursor: "pointer", borderRadius: 4,
                  background: C.panel,
                  opacity: s.hypothetical ? 0.35 : 1,
                  border: `1px solid ${
                    i === cursor ? C.steel : firstDiv != null && s.seq === firstDiv ? C.red : C.line
                  }`,
                  borderBottom: `3px solid ${stepColor(s)}`,
                }}
              />
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
                level {cur.state.level} · {cur.state.tainted_refs.length} tainted ·{" "}
                {cur.state.budget_spent_calls} calls spent
              </div>
            </div>
          )}

          {!fork ? (
            <button
              onClick={() => setFork(true)}
              className="mt-4"
              style={btn({ color: C.steel, fontSize: 12, padding: "8px 14px" })}
            >
              What if… (fork here)
            </button>
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
