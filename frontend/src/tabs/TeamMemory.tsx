// Team memory — what the team's coding agents verifiably learned in each
// repository, given to every agent at the start of its next session there.
//
// Learned only from what the sidecars recorded (memory.py on the plane): a
// check command that passed, a fix after one that failed, and notes people
// wrote. A command something outside wrote, or one that sends, deletes or
// holds a secret, is kept out and listed as such. Found automatically, an item
// is suggested; it turns on by itself once two developers' agents confirmed
// it, and is marked "stopped working" after failing twice.
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, MemoryItem, MemoryRepo } from "../api";
import { navigate } from "../router";
import { C, MONO, btn } from "../theme";

const label: React.CSSProperties = {
  fontFamily: MONO, fontSize: 10.5, color: C.dim, letterSpacing: "0.1em", marginBottom: 8,
};
const panel: React.CSSProperties = { background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 };
const link: React.CSSProperties = {
  background: "none", border: "none", padding: 0, cursor: "pointer", fontFamily: MONO, fontSize: 11, color: C.steel,
};
const day = (ts: string) => (ts ? ts.slice(0, 16).replace("T", " ") : "");

const GROUPS: { status: MemoryItem["status"]; title: string; color: string; words: string }[] = [
  { status: "on", title: "ON — EVERY AGENT GETS THESE", color: C.green, words: "" },
  { status: "suggested", title: "SUGGESTED", color: C.amber,
    words: "Found in one developer's sessions. Turns on by itself once a second developer's agent confirms it — or turn it on now." },
  { status: "stale", title: "STOPPED WORKING", color: C.red,
    words: "Failed twice in a row since it was learned — no longer given. It comes back by itself if it passes again." },
  { status: "off", title: "TURNED OFF", color: C.dim, words: "" },
  { status: "kept_out", title: "KEPT OUT", color: C.dim,
    words: "Passed, but never given to an agent: something outside wrote it, or it sends, deletes or holds a secret." },
];

export default function TeamMemory() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["memory"], queryFn: api.memory });
  const [pick, setPick] = useState<string | null>(null);
  const example = useMutation({
    mutationFn: api.seedMemory,
    onSuccess: (r) => { setPick(r.repo); void qc.invalidateQueries({ queryKey: ["memory"] }); },
  });
  const repos = q.data?.repos ?? [];
  const repo = repos.find((r) => r.repo === pick) ?? repos.find((r) => r.items.length) ?? repos[0];
  return (
    <div data-testid="team-memory" style={{ maxWidth: 860, margin: "0 auto" }}>
      <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 6px" }}>Team memory</h1>
      <div style={{ fontSize: 14, color: C.mut, lineHeight: 1.6, marginBottom: 22 }}>
        What your team's coding agents verifiably learned in each repository — how to run the tests,
        which command fails here and which works — given to every agent at the start of its next session
        there. Only what the sidecars recorded counts: a command that passed, never what an agent said it
        learned. A command that came from a web page, or one that sends, deletes or holds a secret, is
        never handed on.
      </div>

      {repos.length > 1 && (
        <div className="flex gap-2 flex-wrap mb-5" role="tablist">
          {repos.map((r) => (
            <button key={r.repo} onClick={() => setPick(r.repo)} role="tab" aria-selected={r === repo}
              style={btn({ color: r === repo ? C.text : C.dim, borderColor: r === repo ? C.steel : C.line, fontSize: 11 })}>
              {r.label}
            </button>
          ))}
        </div>
      )}

      {repo ? <RepoMemory repo={repo} /> : !q.isLoading && (
        <div data-testid="memory-empty" style={{ ...panel, padding: 16, fontSize: 13, color: C.mut, lineHeight: 1.6 }}>
          Nothing yet. It fills in as coding sessions with the axor hooks run checks in a repository.{" "}
          <button onClick={() => example.mutate()} data-testid="memory-example" style={{ ...link, fontSize: 13 }}>
            {example.isPending ? "loading…" : "see an example →"}
          </button>
        </div>
      )}
      {repo && !repos.some((r) => r.label === "acme/api" && r.items.length) && (
        <div className="mt-6" style={{ fontFamily: MONO, fontSize: 11.5 }}>
          <button onClick={() => example.mutate()} data-testid="memory-example" style={link}>
            {example.isPending ? "loading…" : "see an example →"}
          </button>
        </div>
      )}
      <div className="mt-6" style={{ fontSize: 12, color: C.dim, lineHeight: 1.6 }}>
        Agents get it through the axor hooks: Claude Code at session start, Codex with its first prompt.
        <span style={{ fontFamily: MONO }}> AXOR_TEAM_MEMORY=0</span> keeps it out of one machine's agents.
      </div>
    </div>
  );
}

function RepoMemory({ repo }: { repo: MemoryRepo }) {
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const set = useMutation({
    mutationFn: ({ id, status }: { id: number; status: "on" | "off" }) => api.setMemory(id, status),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["memory"] }),
  });
  const add = useMutation({
    mutationFn: () => api.addMemoryNote(repo.repo, note.trim()),
    onSuccess: () => { setNote(""); void qc.invalidateQueries({ queryKey: ["memory"] }); },
  });
  return (
    <>
      <section className="mb-8" data-testid="memory-preview">
        <div style={label}>WHAT EVERY AGENT GETS AT SESSION START · {repo.label}</div>
        {repo.preview ? (
          <pre style={{ ...panel, padding: "12px 14px", margin: 0, fontFamily: MONO, fontSize: 11.5, color: C.text,
                        whiteSpace: "pre-wrap", lineHeight: 1.6 }}>{repo.preview}</pre>
        ) : (
          <div style={{ ...panel, padding: 14, fontSize: 13, color: C.mut }}>Nothing on yet — agents get nothing from the team's memory here.</div>
        )}
        <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 6 }}>
          given to {repo.sessions_given} session{repo.sessions_given === 1 ? "" : "s"} so far · exactly this text, nothing else
        </div>
      </section>

      {GROUPS.map((g) => {
        const items = repo.items.filter((i) => i.status === g.status);
        if (!items.length) return null;
        return (
          <section key={g.status} className="mb-8" data-testid={`memory-${g.status}`}>
            <div style={{ ...label, color: g.color }}>{g.title}</div>
            {g.words && <div style={{ fontSize: 12.5, color: C.mut, lineHeight: 1.6, marginBottom: 8 }}>{g.words}</div>}
            <div style={panel}>
              {items.map((i, n) => (
                <Item key={i.id} item={i} first={n === 0}
                  onSet={(status) => set.mutate({ id: i.id, status })} busy={set.isPending} />
              ))}
            </div>
          </section>
        );
      })}

      <section className="mb-4" data-testid="memory-note">
        <div style={label}>ADD A NOTE</div>
        <div style={{ fontSize: 12.5, color: C.mut, lineHeight: 1.6, marginBottom: 8 }}>
          Something every agent should know in {repo.label}. It reaches every agent of the team here, as your words — keep it to a line.
        </div>
        <div className="flex gap-2 items-center flex-wrap">
          <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={400}
            onKeyDown={(e) => e.key === "Enter" && note.trim() && add.mutate()}
            placeholder="e.g. run migrations with --dry-run first" aria-label="note for every agent"
            style={{ flex: 1, minWidth: 260, background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4,
                     color: C.text, fontSize: 13, padding: "7px 10px", outline: "none" }} />
          <button onClick={() => add.mutate()} disabled={!note.trim() || add.isPending}
            style={btn({ color: note.trim() ? C.text : C.dim, fontSize: 11 })}>add for the team</button>
        </div>
        {add.error && <div style={{ fontFamily: MONO, fontSize: 11, color: C.red, marginTop: 6 }}>{(add.error as Error).message}</div>}
      </section>
    </>
  );
}

function Item({ item, first, onSet, busy }: {
  item: MemoryItem; first: boolean; onSet: (s: "on" | "off") => void; busy: boolean;
}) {
  const d = item.doc;
  const ev = d.evidence[0];
  const sessions = new Set(d.evidence.map((e) => e.run_id)).size;
  const how = item.kind === "note"
    ? `written by ${d.by || "someone on the team"}`
    : `passed in ${sessions} session${sessions === 1 ? "" : "s"} · ${d.developers.length} developer${d.developers.length === 1 ? "" : "s"} · last ${day(d.last_ok_ts)}`;
  const decided = item.status === "on"
    ? (d.auto ? "on by itself — two developers' agents confirmed it" : d.decided_by ? `turned on by ${d.decided_by}` : "")
    : item.status === "off" && d.decided_by ? `turned off by ${d.decided_by}` : "";
  return (
    <div data-testid={`memory-item-${item.id}`} className="px-4 py-3" style={{ borderTop: first ? "none" : `1px solid ${C.line}` }}>
      <div className="flex items-start gap-3 flex-wrap">
        <span style={{ flex: 1, minWidth: 240, fontSize: 13.5, color: item.status === "kept_out" ? C.mut : C.text, lineHeight: 1.5 }}>
          <MemoryText text={d.text} />
        </span>
        {(item.status === "suggested" || item.status === "off" || item.status === "stale") && (
          <button onClick={() => onSet("on")} disabled={busy} data-testid={`memory-on-${item.id}`}
            style={btn({ color: C.green, borderColor: C.green, fontSize: 10.5, padding: "3px 10px" })}>
            {item.status === "stale" ? "turn back on" : "turn on for the team"}
          </button>
        )}
        {item.status === "on" && (
          <button onClick={() => onSet("off")} disabled={busy} data-testid={`memory-off-${item.id}`}
            style={btn({ fontSize: 10.5, padding: "3px 10px" })}>turn off</button>
        )}
      </div>
      <div className="flex gap-3 flex-wrap" style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 5, lineHeight: 1.6 }}>
        <span>{how}</span>
        {item.status === "kept_out" && <span data-testid="memory-reason" style={{ color: C.amber }}>kept out: {d.reason}</span>}
        {item.status === "stale" && d.last_fail_ts && <span style={{ color: C.red }}>failed {d.fails}× since · last {day(d.last_fail_ts)}</span>}
        {decided && <span>{decided}</span>}
        {d.given > 0 && <span>given to agents {d.given}×</span>}
        {ev && (
          <button onClick={() => navigate(`explain/${ev.run_id}`, { seq: ev.seq, node: ev.node_id })}
            style={{ ...link, fontSize: 10.5 }}>see where it {item.status === "kept_out" ? "ran" : "passed"} →</button>
        )}
      </div>
    </div>
  );
}

/** `backticks` as code. */
function MemoryText({ text }: { text: string }) {
  const parts = text.split(/(`[^`]+`)/g);
  return (
    <>
      {parts.map((p, i) => p.startsWith("`") && p.endsWith("`")
        ? <code key={i} style={{ fontFamily: MONO, fontSize: 12.5, background: "rgba(127,168,204,0.08)", padding: "0 4px", borderRadius: 3 }}>{p.slice(1, -1)}</code>
        : <span key={i}>{p}</span>)}
    </>
  );
}
