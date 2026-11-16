// Replay's front door: find the step. "On Thursday an agent dropped a table in
// prod" — the operator remembers when, roughly what, maybe which agent; not a
// run id. Filters are those memories (GET /v1/runs/search); every row is one
// recorded call or answer, and opens Replay on that very step, where the chain
// it came from is shown. The filters live in the URL, so a search is a link and
// "← back" from a run returns to the same results.
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, FoundStep, StepQuery } from "../api";
import { navigate } from "../router";
import { C, MONO, btn } from "../theme";
import Coach from "../components/Coach";

export const LAST_SEARCH = "axor-replay-search";

type When = "today" | "yesterday" | "7d" | "30d" | "any" | "range";
const WHEN: [When, string][] = [
  ["today", "today"], ["yesterday", "yesterday"], ["7d", "last 7 days"], ["30d", "last 30 days"],
  ["any", "any time"], ["range", "from … to"],
];
const DOES: [NonNullable<StepQuery["does"]>, string][] = [
  ["", "anything"], ["deletes", "deleted or wiped"], ["runs_code", "ran code"],
  ["changes_files", "changed files"], ["sends_out", "sent something out"],
];
const OUTCOME: [NonNullable<StepQuery["outcome"]>, string][] = [
  ["", "anything"], ["let_through", "let through"], ["refused", "refused"],
  ["would_refuse", "would have been refused"], ["outside", "an answer that was outside text"],
];
const OUTCOME_WORDS: Record<FoundStep["outcome"], string> = {
  let_through: "let through", refused: "refused", would_refuse: "would have been refused",
  outside: "answer · outside text", answered: "answer",
};
const DOES_WORDS: Record<string, string> = {
  deletes: "deleted or wiped", runs_code: "ran code", changes_files: "changed files",
  sends_out: "sent out",
};

function startOfDay(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}

function window_(when: When, from: string, to: string): { since?: string; until?: string } {
  const now = new Date();
  const day = 86_400_000;
  switch (when) {
    case "today": return { since: startOfDay(now).toISOString() };
    case "yesterday": {
      const t = startOfDay(now);
      return { since: new Date(t.getTime() - day).toISOString(), until: t.toISOString() };
    }
    case "7d": return { since: new Date(now.getTime() - 7 * day).toISOString() };
    case "30d": return { since: new Date(now.getTime() - 30 * day).toISOString() };
    case "range": return {
      since: from ? new Date(from).toISOString() : undefined,
      until: to ? new Date(to).toISOString() : undefined,
    };
    default: return {};
  }
}

function stamp(ts: string | null): string {
  if (!ts) return "—";
  const d = new Date(ts);
  return `${d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })} ${d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" })}`;
}

const chip = (on: boolean): React.CSSProperties => btn({
  background: on ? "rgba(127,168,204,0.12)" : "none", border: `1px solid ${on ? C.steel : C.line}`,
  color: on ? C.steel : C.mut, fontSize: 11, padding: "3px 9px",
});
const label: React.CSSProperties = { fontFamily: MONO, fontSize: 10.5, color: C.dim, width: 92, flexShrink: 0, paddingTop: 5 };
const field: React.CSSProperties = {
  background: C.panel2, border: `1px solid ${C.line}`, borderRadius: 5, color: C.text,
  fontFamily: MONO, fontSize: 11.5, padding: "4px 8px",
};

export default function ReplaySearch({ query }: { query: Record<string, string> }) {
  const qc = useQueryClient();
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.listRuns });
  const when = (query.when as When) || "7d";
  const set = (patch: Record<string, string>) => {
    const next = { ...query, ...patch };
    sessionStorage.setItem(LAST_SEARCH, JSON.stringify(next));
    navigate("explain", next);
  };
  // the text boxes type freely and search a moment after the last key
  const [text, setText] = useState(query.text ?? "");
  const [tool, setTool] = useState(query.tool ?? "");
  useEffect(() => setText(query.text ?? ""), [query.text]);
  useEffect(() => setTool(query.tool ?? ""), [query.tool]);
  useEffect(() => {
    if (text === (query.text ?? "") && tool === (query.tool ?? "")) return;
    const t = setTimeout(() => set({ text, tool }), 350);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, tool]);

  const q: StepQuery = useMemo(() => ({
    ...window_(when, query.from ?? "", query.to ?? ""),
    agent: query.agent, tool: query.tool, text: query.text,
    does: (query.does ?? "") as StepQuery["does"],
    outcome: (query.outcome ?? "") as StepQuery["outcome"],
  }), [when, query.from, query.to, query.agent, query.tool, query.text, query.does, query.outcome]);
  const found = useQuery({ queryKey: ["step-search", q], queryFn: () => api.searchSteps(q) });

  const agents = useMemo(
    () => [...new Set((runs.data ?? []).map((r) => r.node_id))].sort(),
    [runs.data],
  );
  const seed = useMutation({
    mutationFn: async () => {
      await api.seedAdapterRuns();
      return api.seedProdIncident();
    },
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["runs"] });
      await qc.invalidateQueries({ queryKey: ["step-search"] });
      set({ when: "30d", does: "deletes", outcome: "", agent: "", text: "", tool: "" });
    },
  });
  const nothingYet = runs.isSuccess && runs.data.length === 0;
  const steps = found.data?.steps ?? [];
  const open = (s: FoundStep) => {
    // what is typed counts even if the search had not caught up with it yet
    sessionStorage.setItem(LAST_SEARCH, JSON.stringify({ ...query, text, tool }));
    navigate(`explain/${encodeURIComponent(s.run_id)}`, { seq: s.seq, node: s.node_id });
  };

  return (
    <div style={{ maxWidth: 900, margin: "0 auto" }}>
      <Coach id="replay" title="Replay — find the step, see where it came from">
        Something went wrong on Thursday? Pick when, what the agent did, any text you remember
        (a table, a host, a command). Every row is one call an agent made; open it to see the run
        step by step and where what it used came from.
      </Coach>
      <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 4px" }}>Replay</h1>
      <div style={{ fontSize: 14, color: C.mut, marginBottom: 16 }}>
        Find the step — by when, which agent, what it did. Open it to see where it came from.
      </div>

      <div data-testid="search-filters" className="p-4 mb-4"
        style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8, display: "grid", gap: 10 }}>
        <div className="flex gap-2">
          <span style={label}>when</span>
          <div className="flex gap-1.5 flex-wrap items-center">
            {WHEN.map(([id, l]) => (
              <button key={id} style={chip(when === id)} onClick={() => set({ when: id })}>{l}</button>
            ))}
            {when === "range" && (
              <>
                <input type="datetime-local" aria-label="from" value={query.from ?? ""} style={field}
                  onChange={(e) => set({ from: e.target.value })} />
                <span style={{ color: C.dim }}>→</span>
                <input type="datetime-local" aria-label="to" value={query.to ?? ""} style={field}
                  onChange={(e) => set({ to: e.target.value })} />
              </>
            )}
          </div>
        </div>
        <div className="flex gap-2">
          <span style={label}>what it did</span>
          <div className="flex gap-1.5 flex-wrap">
            {DOES.map(([id, l]) => (
              <button key={id || "any"} style={chip((query.does ?? "") === id)} onClick={() => set({ does: id })}>{l}</button>
            ))}
          </div>
        </div>
        <div className="flex gap-2">
          <span style={label}>what happened</span>
          <div className="flex gap-1.5 flex-wrap">
            {OUTCOME.map(([id, l]) => (
              <button key={id || "any"} style={chip((query.outcome ?? "") === id)} onClick={() => set({ outcome: id })}>{l}</button>
            ))}
          </div>
        </div>
        <div className="flex gap-2 items-center flex-wrap">
          <span style={label}>agent</span>
          <select aria-label="agent" value={query.agent ?? ""} onChange={(e) => set({ agent: e.target.value })}
            style={{ ...field, minWidth: 200 }}>
            <option value="">any agent</option>
            {agents.map((a) => <option key={a} value={a}>{a}</option>)}
          </select>
          <span style={{ ...label, width: "auto", paddingTop: 0, marginLeft: 12 }}>tool</span>
          <input aria-label="tool" placeholder="bash, slack_post…" value={tool} onChange={(e) => setTool(e.target.value)}
            style={{ ...field, width: 150 }} />
        </div>
        <div className="flex gap-2 items-center">
          <span style={label}>contains</span>
          <input aria-label="contains" placeholder="prod, DROP TABLE, rm -rf, a host, a file…" value={text}
            onChange={(e) => setText(e.target.value)} style={{ ...field, flex: 1 }} />
        </div>
      </div>

      {nothingYet ? (
        <div style={{ fontSize: 13.5, color: C.mut, lineHeight: 1.6 }}>
          Nothing has run yet — connect an agent and every call it makes is searchable here.{" "}
          <button onClick={() => seed.mutate()} disabled={seed.isPending} data-testid="search-example"
            style={btn({ color: C.steel, fontSize: 11.5, padding: "4px 10px", marginLeft: 6 })}>
            {seed.isPending ? "loading…" : "load an example: an agent dropped a production table"}
          </button>
        </div>
      ) : found.isError ? (
        <div style={{ color: C.red, fontSize: 13 }}>{(found.error as Error).message}</div>
      ) : (
        <>
          <div className="flex items-center gap-3 mb-2" style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>
            <span data-testid="search-count">
              {found.isPending ? "searching…" : `${steps.length}${steps.length >= 200 ? "+" : ""} step${steps.length === 1 ? "" : "s"}`}
            </span>
            {found.data?.more && <span style={{ color: C.amber }}>only the newest {found.data.scanned.toLocaleString()} calls were searched — narrow the time</span>}
            <span style={{ flex: 1 }} />
            {!runs.data?.some((r) => r.run_id === "ex_prod_drop") && (
              <button onClick={() => seed.mutate()} disabled={seed.isPending} data-testid="search-example"
                style={{ background: "none", border: "none", padding: 0, cursor: "pointer", fontFamily: MONO, fontSize: 11, color: C.steel }}>
                {seed.isPending ? "loading…" : "an example: an agent dropped a production table"}
              </button>
            )}
          </div>
          {steps.length === 0 && found.isSuccess ? (
            <div style={{ fontSize: 13.5, color: C.mut, padding: "14px 0" }}>
              Nothing matches. Try a wider time, or fewer filters.
            </div>
          ) : (
            <div data-testid="search-results" style={{ border: `1px solid ${C.line}`, borderRadius: 8, overflow: "hidden" }}>
              {steps.map((s, i) => {
                const color = s.outcome === "refused" ? C.red
                  : s.outcome === "would_refuse" || s.outcome === "outside" ? C.amber
                    : s.does.includes("deletes") ? C.red : C.green;
                return (
                  <div key={`${s.run_id}:${s.node_id}:${s.seq}`} onClick={() => open(s)} data-testid="search-row"
                    style={{ display: "grid", gridTemplateColumns: "160px 170px 1fr 150px", gap: 12, alignItems: "baseline",
                             padding: "9px 12px", cursor: "pointer", background: i % 2 ? C.panel : C.panel2,
                             borderLeft: `3px solid ${color}` }}>
                    <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>{stamp(s.ts)}</span>
                    <span style={{ fontFamily: MONO, fontSize: 11, color: C.text, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                      title={s.node_id}>{s.node_id}</span>
                    <span style={{ minWidth: 0 }}>
                      <span style={{ fontFamily: MONO, fontSize: 11.5, color: C.text }}>{s.tool}{s.kind === "tool_result" ? " answered" : ""}</span>
                      {s.what && (
                        <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginLeft: 8, overflowWrap: "anywhere" }}>
                          {s.what.length > 140 ? `${s.what.slice(0, 140)}…` : s.what}
                        </span>
                      )}
                      {(s.does.length > 0 || (s.from_outside && s.kind === "tool_call")) && (
                        <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, marginTop: 2 }}>
                          {s.does.map((d) => DOES_WORDS[d] ?? d).join(" · ")}
                          {s.from_outside && s.kind === "tool_call" && (
                            <span style={{ color: C.amber }}>{s.does.length ? " · " : ""}used text from outside</span>
                          )}
                        </div>
                      )}
                    </span>
                    <span style={{ fontFamily: MONO, fontSize: 11, color, textAlign: "right" }}>
                      {OUTCOME_WORDS[s.outcome]} →
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </>
      )}
    </div>
  );
}
