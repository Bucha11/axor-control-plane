// Chaos tests — break an agent's tool on purpose and see what it says: does it
// admit the tool failed, or make something up? One place for every such test
// (an example agent, your agent through the proxy, your live agents on their
// next run), what each found, and the schedule that keeps breaking things on
// its own (GET/POST/PUT /v1/chaos — backend chaos.py).
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ChaosRun, ChaosVerdict } from "../api";
import { navigate } from "../router";
import { C, MONO, btn } from "../theme";
import { useApp } from "../store";
import Coach from "../components/Coach";

export const MODE_WORDS: Record<string, string> = {
  silent_fail: "returning an error",
  corrupt_retrieval: "poisoning its results",
  instruction_injection: "hiding an instruction in its output",
  tool_substitution: "answering from somewhere else",
  timeout: "not answering in time",
  empty_result: "answering with nothing",
};

/** The ways to break a tool that this deployment's engine knows (axor-eval
 * names timeout and empty_result from the release after 0.2.2) — so a screen
 * never offers a way the plane would refuse. */
export function useFaultModes(): string[] {
  const q = useQuery({ queryKey: ["chaos"], queryFn: api.chaos, staleTime: 60_000 });
  return q.data?.modes ?? ["silent_fail", "corrupt_retrieval", "instruction_injection", "tool_substitution"];
}
const VERDICT: Record<ChaosVerdict, { text: string; color: string; hint: string }> = {
  made_it_up: { text: "made it up", color: C.red, hint: "what it said contradicts what the broken tool returned" },
  stopped: { text: "stopped", color: C.amber, hint: "it made something up, and Axor stopped it before it spread" },
  no_lie_found: { text: "no lie found", color: C.green, hint: "nothing it said contradicts the trace — not proof it was honest" },
  no_answer: { text: "no answer yet", color: C.dim, hint: "still running, or it never said anything" },
};

const section: React.CSSProperties = { fontFamily: MONO, fontSize: 10.5, color: C.dim, letterSpacing: "0.1em", margin: "22px 0 8px" };
const card: React.CSSProperties = { background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8, padding: 14 };
const field: React.CSSProperties = {
  background: C.panel2, border: `1px solid ${C.line}`, borderRadius: 5, color: C.text,
  fontFamily: MONO, fontSize: 11.5, padding: "4px 8px",
};
const link: React.CSSProperties = { background: "none", border: "none", padding: 0, cursor: "pointer",
  fontFamily: MONO, fontSize: 11, color: C.steel };
const chip = (on: boolean): React.CSSProperties => btn({
  background: on ? "rgba(127,168,204,0.12)" : "none", border: `1px solid ${on ? C.steel : C.line}`,
  color: on ? C.steel : C.mut, fontSize: 11, padding: "3px 9px",
});

export default function ChaosTests() {
  const qc = useQueryClient();
  const connect = useApp((st) => st.connect);
  // the example agent runs in demo mode — the same switch Home and Incidents make
  const example = () => { connect("demo"); navigate("catch", { auto: "1" }); };
  const data = useQuery({ queryKey: ["chaos"], queryFn: api.chaos });
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.listRuns });
  const agents = useMemo(() => [...new Set((runs.data ?? []).map((r) => r.node_id))].sort(), [runs.data]);
  const list = data.data?.runs ?? [];
  const counts = list.reduce<Record<string, number>>((m, r) => ({ ...m, [r.verdict]: (m[r.verdict] ?? 0) + 1 }), {});

  return (
    <div style={{ maxWidth: 820, margin: "0 auto" }}>
      <Coach id="chaos" title="Chaos tests — break a tool, see what the agent says">
        A tool is made to fail on purpose — an error, poisoned results, a hidden instruction, a
        different backend answering. Then what the agent said is checked against what the tool
        really returned. An agent that says “done” when the tool failed will do it for real one day.
      </Coach>
      <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 4px" }}>Chaos tests</h1>
      <div style={{ fontSize: 14, color: C.mut, marginBottom: 6, lineHeight: 1.6 }}>
        Break a tool on purpose and see what your agent says — does it admit the tool failed, or make
        something up?
      </div>

      <div style={section}>RUN ONE</div>
      <div className="flex flex-col gap-3">
        <div style={card}>
          <div style={{ fontSize: 13.5, fontWeight: 600, marginBottom: 4 }}>On an agent you run here</div>
          <div style={{ fontSize: 12.5, color: C.mut, lineHeight: 1.6, marginBottom: 10 }}>
            The example agent, or yours pointed at the proxy: pick a tool and how to break it, watch the run.
          </div>
          <div className="flex gap-2 flex-wrap">
            <button data-testid="chaos-example" onClick={example}
              style={btn({ color: C.text, borderColor: C.steel, fontSize: 11.5, padding: "5px 12px" })}>
              try it on the example →
            </button>
            <button onClick={() => navigate("catch")} style={btn({ color: C.mut, fontSize: 11.5, padding: "5px 12px" })}>
              on my agent →
            </button>
          </div>
        </div>
        <LiveArm agents={agents} modes={data.data?.modes ?? Object.keys(MODE_WORDS)}
          onArmed={() => void qc.invalidateQueries({ queryKey: ["chaos"] })} />
      </div>

      <div style={section}>ON A SCHEDULE</div>
      {data.data && <Schedule agents={agents} modes={data.data.modes} current={data.data.schedule}
        onSaved={() => void qc.invalidateQueries({ queryKey: ["chaos"] })} />}

      <div style={section}>WHAT THEY FOUND</div>
      {data.isLoading ? (
        <div style={{ color: C.mut, fontSize: 13 }}>loading…</div>
      ) : list.length === 0 ? (
        <div style={{ color: C.mut, fontSize: 13.5 }}>
          No chaos test has run yet.{" "}
          <button onClick={example} style={{ ...link, fontSize: 13 }}>try it on the example →</button>
        </div>
      ) : (
        <>
          <div data-testid="chaos-counts" className="flex gap-3 mb-2" style={{ fontFamily: MONO, fontSize: 11.5, color: C.dim }}>
            {list.length} test{list.length === 1 ? "" : "s"}
            {(Object.keys(VERDICT) as ChaosVerdict[]).filter((v) => counts[v]).map((v) => (
              <span key={v} style={{ color: VERDICT[v].color }}>· {counts[v]} {VERDICT[v].text}</span>
            ))}
          </div>
          <div data-testid="chaos-runs" style={{ border: `1px solid ${C.line}`, borderRadius: 8, overflow: "hidden" }}>
            {list.map((r, i) => <Row key={r.run_id} r={r} odd={i % 2 === 1} />)}
          </div>
        </>
      )}
    </div>
  );
}

function Row({ r, odd }: { r: ChaosRun; odd: boolean }) {
  const v = VERDICT[r.verdict];
  const broke = r.faults.length
    ? r.faults.map((f) => `${f.tool} — ${MODE_WORDS[f.mode] ?? f.mode}`).join("; ")
    : r.scenario.replace(/-/g, " ");
  return (
    <div data-testid="chaos-row" style={{ display: "grid", gridTemplateColumns: "130px 1fr 130px", gap: 12,
      padding: "10px 12px", background: odd ? C.panel : C.panel2, borderLeft: `3px solid ${v.color}` }}>
      <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>
        {new Date(r.created_ts).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" })}
        <div style={{ color: C.text, marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={r.node_id}>{r.node_id}</div>
      </div>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 13 }}>broke <span style={{ fontFamily: MONO }}>{broke}</span></div>
        {r.claim && (
          <div style={{ fontSize: 12.5, color: C.mut, marginTop: 3 }}>
            it said: “{r.claim.length > 160 ? `${r.claim.slice(0, 160)}…` : r.claim}”
          </div>
        )}
        <div className="flex gap-3 mt-1">
          <button onClick={() => navigate(`catch/${encodeURIComponent(r.run_id)}`)} style={link}>the receipt →</button>
          <button onClick={() => navigate(`explain/${encodeURIComponent(r.run_id)}`)} style={link}>replay it →</button>
        </div>
      </div>
      <div title={v.hint} style={{ fontFamily: MONO, fontSize: 11.5, color: v.color, textAlign: "right" }}>{v.text}</div>
    </div>
  );
}

function LiveArm({ agents, modes, onArmed }: { agents: string[]; modes: string[]; onArmed: () => void }) {
  const [picked, setPicked] = useState<string[]>([]);
  const [tool, setTool] = useState("");
  const [mode, setMode] = useState("");
  const tools = useQuery({
    queryKey: ["chaos-tools", picked[0]],
    queryFn: () => api.chaosTools(picked[0]),
    enabled: picked.length === 1,
  });
  const arm = useMutation({
    mutationFn: () => api.chaosArm(picked, tool.trim() || undefined, mode || undefined),
    onSuccess: onArmed,
  });
  return (
    <div style={card} data-testid="chaos-live">
      <div style={{ fontSize: 13.5, fontWeight: 600, marginBottom: 4 }}>On your live agents, next run</div>
      <div style={{ fontSize: 12.5, color: C.mut, lineHeight: 1.6, marginBottom: 8 }}>
        Only agents that run with <span style={{ fontFamily: MONO, color: C.text }}>AXOR_TEST_BENCH=1</span> take it —
        breaking a tool spoils a real answer.
      </div>
      {agents.length === 0 ? (
        <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>no agent has run yet</div>
      ) : (
        <>
          <div className="flex gap-1.5 flex-wrap mb-2">
            {agents.map((a) => (
              <button key={a} style={chip(picked.includes(a))}
                onClick={() => setPicked(picked.includes(a) ? picked.filter((x) => x !== a) : [...picked, a])}>
                {picked.includes(a) ? "✓ " : ""}{a}
              </button>
            ))}
          </div>
          <div className="flex gap-2 flex-wrap items-center mb-2">
            <input list="chaos-tools" value={tool} onChange={(e) => setTool(e.target.value)} aria-label="tool to break"
              placeholder={picked.length === 1 ? "any tool it used" : "any tool each used"} style={{ ...field, width: 150 }} />
            <datalist id="chaos-tools">{(tools.data?.tools ?? []).map((t) => <option key={t} value={t} />)}</datalist>
            <select value={mode} onChange={(e) => setMode(e.target.value)} aria-label="how to break it" style={field}>
              <option value="">any way</option>
              {modes.map((m) => <option key={m} value={m}>{MODE_WORDS[m] ?? m}</option>)}
            </select>
          </div>
          <button data-testid="chaos-arm" onClick={() => arm.mutate()} disabled={!picked.length || arm.isPending}
            style={btn({ color: picked.length ? C.text : C.dim, borderColor: picked.length ? C.steel : C.line, fontSize: 11.5, padding: "5px 12px" })}>
            {arm.isPending ? "…" : "break it on the next run"}
          </button>
        </>
      )}
      {arm.data && (
        <div data-testid="chaos-armed" className="mt-2" style={{ fontFamily: MONO, fontSize: 11, lineHeight: 1.6 }}>
          {arm.data.armed.map((a) => (
            <div key={a.node_id} style={{ color: C.amber }}>
              armed: {a.node_id} — {a.faults.map((f) => `${f.tool}, ${MODE_WORDS[f.mode] ?? f.mode}`).join("; ")}
            </div>
          ))}
          {arm.data.skipped.map((s) => <div key={s.node_id} style={{ color: C.dim }}>{s.node_id}: {s.reason}</div>)}
        </div>
      )}
      {arm.isError && <div className="mt-2" style={{ color: C.red, fontSize: 12 }}>{(arm.error as Error).message}</div>}
    </div>
  );
}

function Schedule({ agents, modes, current, onSaved }: {
  agents: string[]; modes: string[]; current: import("../api").ChaosSchedule; onSaved: () => void;
}) {
  const [enabled, setEnabled] = useState(current.enabled);
  const [hours, setHours] = useState(current.interval_hours ?? 24);
  const [nodes, setNodes] = useState<string[]>(current.nodes);
  const [ways, setWays] = useState<string[]>(current.modes.length ? current.modes : modes);
  const save = useMutation({
    mutationFn: () => api.chaosSchedule({ enabled, interval_hours: hours, nodes, modes: ways }),
    onSuccess: onSaved,
  });
  const locked = !current.ee_active;
  return (
    <div style={{ ...card, opacity: locked ? 0.75 : 1 }} data-testid="chaos-schedule">
      <div className="flex items-center gap-2 flex-wrap" style={{ fontSize: 13, color: C.text, lineHeight: 2 }}>
        <label className="flex items-center gap-1.5" style={{ cursor: locked ? "default" : "pointer" }}>
          <input type="checkbox" checked={enabled} disabled={locked} onChange={(e) => setEnabled(e.target.checked)} />
          every
        </label>
        <input type="number" min={1} value={hours} disabled={locked} aria-label="every how many hours"
          onChange={(e) => setHours(Math.max(1, Number(e.target.value) || 1))} style={{ ...field, width: 60 }} />
        hours, break a tool each agent has used —
        <span style={{ color: C.mut }}>{nodes.length ? `${nodes.length} chosen` : "every agent"}</span>
      </div>
      <div className="flex gap-1.5 flex-wrap mt-1">
        {agents.map((a) => (
          <button key={a} disabled={locked} style={chip(nodes.includes(a))}
            onClick={() => setNodes(nodes.includes(a) ? nodes.filter((x) => x !== a) : [...nodes, a])}>
            {nodes.includes(a) ? "✓ " : ""}{a}
          </button>
        ))}
      </div>
      <div className="flex gap-1.5 flex-wrap mt-2 items-center">
        <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>in one of these ways:</span>
        {modes.map((m) => (
          <button key={m} disabled={locked} style={chip(ways.includes(m))}
            onClick={() => setWays(ways.includes(m) ? (ways.length > 1 ? ways.filter((x) => x !== m) : ways) : [...ways, m])}>
            {MODE_WORDS[m] ?? m}
          </button>
        ))}
      </div>
      <div className="flex items-center gap-3 mt-3" style={{ fontFamily: MONO, fontSize: 11 }}>
        {locked ? (
          <span style={{ color: C.mut }}>
            scheduled chaos tests are part of Team —{" "}
            <button onClick={() => navigate("pricing")} style={link}>see plans →</button>
          </span>
        ) : (
          <button data-testid="chaos-schedule-save" onClick={() => save.mutate()} disabled={save.isPending}
            style={btn({ color: C.text, borderColor: C.steel, fontSize: 11.5, padding: "4px 12px" })}>
            {save.isPending ? "…" : "save"}
          </button>
        )}
        {save.isSuccess && <span style={{ color: C.green }}>saved</span>}
        {save.isError && <span style={{ color: C.red }}>{(save.error as Error).message}</span>}
        {current.last_run_ts && (
          <span style={{ color: C.dim }}>
            last: {new Date(current.last_run_ts).toLocaleString()} —{" "}
            {current.last_armed.length
              ? current.last_armed.map((a) => `${a.node_id}: ${a.tool}`).join(", ")
              : "nothing armed"}
          </span>
        )}
      </div>
    </div>
  );
}
