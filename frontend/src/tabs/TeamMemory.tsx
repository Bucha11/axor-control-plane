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
import { api, MemoryChange, MemoryItem, MemoryRepo } from "../api";
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
    words: "Since it last passed it failed for two developers, or three times for one — no longer given. It comes back by itself if it passes again." },
  { status: "dormant", title: "NOT CONFIRMED LATELY", color: C.dim,
    words: "Nobody's agent ran it within the freshness window — not given until it passes again, or someone vouches for it." },
  { status: "replaced", title: "REPLACED", color: C.dim,
    words: "A newer command of the same kind started passing and this one hasn't passed since — the team moved on." },
  { status: "off", title: "TURNED OFF", color: C.dim, words: "" },
  { status: "kept_out", title: "KEPT OUT", color: C.dim,
    words: "Passed, but never given to an agent: something outside wrote it; it sends, deletes or holds a secret; or it makes the check pass without the code passing it — updates snapshots, swallows the failure, leaves tests out." },
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
  const freshDays = q.data?.fresh_days ?? 30;
  const repo = repos.find((r) => r.repo === pick) ?? repos.find((r) => r.items.length) ?? repos[0];
  return (
    <div data-testid="team-memory" style={{ maxWidth: 860, margin: "0 auto" }}>
      <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 6px" }}>Team memory</h1>
      <div style={{ fontSize: 14, color: C.mut, lineHeight: 1.6, marginBottom: 22 }}>
        What your team's coding agents verifiably learned in each repository — how to run the tests,
        which command fails here and which works — given to every agent at the start of its next session
        there. Only what the sidecars recorded counts: a command that passed, never what an agent said it
        learned. A command that came from a web page, one that sends, deletes or holds a secret, or one
        that turns a check green without fixing anything (<code style={{ fontFamily: MONO, fontSize: 12.5 }}>jest -u</code>,{" "}
        <code style={{ fontFamily: MONO, fontSize: 12.5 }}>|| true</code>,{" "}
        <code style={{ fontFamily: MONO, fontSize: 12.5 }}>-k "not …"</code>) is never handed on.
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

      {repo ? <RepoMemory repo={repo} freshDays={freshDays} /> : !q.isLoading && (
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

function RepoMemory({ repo, freshDays }: { repo: MemoryRepo; freshDays: number }) {
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const fresh = useMutation({
    mutationFn: (days: number) => api.setMemorySettings(days),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["memory"] }),
  });
  const set = useMutation({
    mutationFn: ({ id, status }: { id: number; status: "on" | "off" }) => api.setMemory(id, status),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["memory"] }),
  });
  const add = useMutation({
    mutationFn: () => api.addMemoryNote(repo.repo, note.trim()),
    onSuccess: () => { setNote(""); void qc.invalidateQueries({ queryKey: ["memory"] }); },
  });
  const given = new Set(repo.given_ids);
  return (
    <>
      {!repo.given && (
        <section data-testid="memory-plan" className="mb-8 p-4" style={{ ...panel, borderLeft: `3px solid ${C.amber}` }}>
          <div style={{ fontSize: 15, fontWeight: 650, color: C.amber, marginBottom: 4 }}>
            Team memory is on the Team plan
          </div>
          <div style={{ fontSize: 13, color: C.mut, lineHeight: 1.6 }}>
            Agents in this workspace get nothing from it yet. What it learns is kept — on the Team plan every
            agent gets it from its next session.{" "}
            <button onClick={() => navigate("pricing")} style={{ ...link, fontSize: 12 }}>see plans →</button>
          </div>
        </section>
      )}
      {repo.drift && <Drift change={repo.drift} onOff={(id) => set.mutate({ id, status: "off" })} />}
      <section className="mb-8" data-testid="memory-preview">
        <div style={label}>{repo.given ? "WHAT EVERY AGENT GETS AT SESSION START" : "WHAT EVERY AGENT WOULD GET ON THE TEAM PLAN"} · {repo.label}</div>
        {repo.preview ? (
          <pre style={{ ...panel, padding: "12px 14px", margin: 0, fontFamily: MONO, fontSize: 11.5, color: C.text,
                        whiteSpace: "pre-wrap", lineHeight: 1.6 }}>{repo.preview}</pre>
        ) : (
          <div style={{ ...panel, padding: 14, fontSize: 13, color: C.mut }}>Nothing on yet — agents get nothing from the team's memory here.</div>
        )}
        {repo.given && (
          <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 6 }}>
            given to {repo.sessions_given} session{repo.sessions_given === 1 ? "" : "s"} so far · exactly this text, nothing else
          </div>
        )}
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
                <Item key={i.id} item={i} first={n === 0} overLimit={i.status === "on" && !given.has(i.id)}
                  onSet={(status) => set.mutate({ id: i.id, status })} busy={set.isPending} />
              ))}
            </div>
          </section>
        );
      })}

      {repo.changes.length > 0 && <Changes changes={repo.changes} />}

      <section className="mb-8" data-testid="memory-fresh">
        <div style={label}>ROTATION</div>
        <div className="flex items-center gap-2 flex-wrap" style={{ fontSize: 12.5, color: C.mut, lineHeight: 1.6 }}>
          A fact nobody's agent confirmed for
          <select value={freshDays} onChange={(e) => fresh.mutate(Number(e.target.value))} aria-label="freshness window"
            style={{ background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: C.text, fontFamily: MONO, fontSize: 11.5, padding: "2px 6px" }}>
            {[7, 14, 30, 60, 90].map((d) => <option key={d} value={d}>{d} days</option>)}
          </select>
          is no longer given. Suggestions nobody confirmed are dropped after 14 days; people's notes ask "still true?" after 90.
        </div>
      </section>

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

const daysSince = (ts?: string) => (ts ? Math.floor((Date.now() - new Date(ts).getTime()) / 86400000) : 0);

function Item({ item, first, onSet, busy, overLimit }: {
  item: MemoryItem; first: boolean; onSet: (s: "on" | "off") => void; busy: boolean; overLimit: boolean;
}) {
  const d = item.doc;
  const noteAge = item.kind === "note" ? daysSince(d.vouched_ts || d.last_ok_ts) : 0;
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
        {["suggested", "off", "stale", "dormant", "replaced"].includes(item.status) && (
          <button onClick={() => onSet("on")} disabled={busy} data-testid={`memory-on-${item.id}`}
            style={btn({ color: C.green, borderColor: C.green, fontSize: 10.5, padding: "3px 10px" })}>
            {item.status === "suggested" || item.status === "off" ? "turn on for the team"
              : item.status === "dormant" ? "still true — vouch for it" : "turn back on"}
          </button>
        )}
        {item.status === "on" && noteAge >= 90 && (
          <button onClick={() => onSet("on")} disabled={busy} data-testid={`memory-vouch-${item.id}`}
            style={btn({ color: C.amber, borderColor: C.amber, fontSize: 10.5, padding: "3px 10px" })}>
            still true
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
        {overLimit && <span data-testid="memory-over-limit" style={{ color: C.amber }}>not given: over the 1500-character limit</span>}
        {item.status === "replaced" && d.replaced_by && <span>replaced by {d.replaced_by}</span>}
        {item.kind === "note" && noteAge >= 90 && <span style={{ color: C.amber }}>written {noteAge} days ago — still true?</span>}
        {!ev && (d.evidence_pruned ?? 0) > 0 && <span>its sessions are older than the plane keeps</span>}
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

/** Agents did significantly worse after a change of what they get. */
function Drift({ change, onOff }: { change: MemoryChange; onOff: (id: number) => void }) {
  const c = change.compare!;
  return (
    <section data-testid="memory-drift" className="mb-8 p-4"
      style={{ ...panel, borderLeft: `3px solid ${C.red}` }}>
      <div style={{ fontSize: 15, fontWeight: 650, color: C.red, marginBottom: 4 }}>
        Agents did worse after the team memory changed — {day(change.ts)}
      </div>
      <div style={{ fontSize: 13, color: C.mut, lineHeight: 1.6, marginBottom: 8 }}>
        Sessions that went wrong (their first check failed, or the agent said the checks passed when they
        failed): {c.before.bad} of {c.before.sessions} before, {c.after.bad} of {c.after.sessions} after
        {c.p != null ? ` (p ${c.p < 0.001 ? "<0.001" : c.p.toPrecision(2)})` : ""}. A sign, not a proof — check it in a sandbox.
      </div>
      {change.added.map((a) => (
        <div key={a.id} className="flex items-center gap-3 flex-wrap" style={{ padding: "3px 0" }}>
          <span style={{ fontSize: 13, color: C.text, flex: 1 }}>added: <MemoryText text={a.text} /></span>
          <button onClick={() => onOff(a.id)} data-testid={`memory-drift-off-${a.id}`}
            style={btn({ fontSize: 10.5, padding: "3px 10px" })}>turn off</button>
        </div>
      ))}
      {change.removed.map((r) => (
        <div key={r.id} style={{ fontSize: 13, color: C.mut, padding: "3px 0" }}>removed ({r.why}): <MemoryText text={r.text} /></div>
      ))}
      <button onClick={() => navigate("health")} style={{ ...link, marginTop: 8 }}>check it in a sandbox — context health →</button>
    </section>
  );
}

const VERDICT_WORDS: Record<string, [string, string]> = {
  worse: ["worse after", C.red], better: ["better after", C.green],
  same: ["no difference", C.dim], too_few: ["too few sessions to tell", C.dim],
};

/** Every change of what agents get, and how the sessions went before and after. */
function Changes({ changes }: { changes: MemoryChange[] }) {
  return (
    <section className="mb-8" data-testid="memory-changes">
      <div style={label}>CHANGES — WHAT AGENTS GET, AND HOW THEY DID</div>
      <div style={panel}>
        {changes.map((ch, n) => {
          const v = ch.compare ? VERDICT_WORDS[ch.compare.verdict] : null;
          return (
            <div key={ch.id} className="px-4 py-2" style={{ borderTop: n ? `1px solid ${C.line}` : "none", fontSize: 12.5 }}>
              <div className="flex gap-3 flex-wrap" style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>
                <span>{day(ch.ts)}</span>
                <span>v {ch.version || "empty"}</span>
                {v && <span style={{ color: v[1] }}>{v[0]}
                  {ch.compare && ch.compare.verdict !== "too_few"
                    ? ` · went wrong ${ch.compare.before.bad}/${ch.compare.before.sessions} → ${ch.compare.after.bad}/${ch.compare.after.sessions}`
                    : ch.compare ? ` · ${ch.compare.before.sessions} before, ${ch.compare.after.sessions} after` : ""}</span>}
              </div>
              {ch.added.map((a) => <div key={`a${a.id}`} style={{ color: C.text }}>+ <MemoryText text={a.text} /> <span style={{ color: C.dim, fontSize: 11 }}>· {a.why}</span></div>)}
              {ch.removed.map((r) => <div key={`r${r.id}`} style={{ color: C.mut }}>− <MemoryText text={r.text} /> <span style={{ color: C.dim, fontSize: 11 }}>· {r.why}{r.replaced_by ? ` by ${r.replaced_by}` : ""}</span></div>)}
            </div>
          );
        })}
      </div>
    </section>
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
