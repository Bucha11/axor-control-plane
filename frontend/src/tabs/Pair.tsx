// Pair sessions — someone's coding-agent session, watched live by others who
// can send the agent a note. The developer starts a pair on their session and
// shares the link; whoever opens it sees each step as it happens and can write
// a note, which the developer's axor sidecar gives the agent at its next step
// (beside the tool result, or with the next prompt). Teammates join on any
// plan; people from outside the team — their own account, a free one too —
// only when the pair was opened for them and the inviting workspace pays.
// Two sessions side by side: ?with=<another pair's token>.
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy } from "lucide-react";
import { api, PairFeed, PairNote, PairStep } from "../api";
import { navigate } from "../router";
import { C, MONO, btn } from "../theme";
import { HowItWorks } from "../components/MachineCommand";

const label: React.CSSProperties = {
  fontFamily: MONO, fontSize: 10.5, color: C.dim, letterSpacing: "0.1em", marginBottom: 8,
};
const panel: React.CSSProperties = { background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 };
const link: React.CSSProperties = {
  background: "none", border: "none", padding: 0, cursor: "pointer", fontFamily: MONO, fontSize: 11, color: C.steel,
};

const clock = (ts: string) => {
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? ts.slice(11, 19) : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
};
const short = (node: string) => (node.length > 28 ? `${node.slice(0, 26)}…` : node);
const pairUrl = (token: string) =>
  `${typeof window === "undefined" ? "" : window.location.origin + window.location.pathname}#/pair/${token}`;

// pair links this browser has opened, for "side by side" — a guest from
// outside the team cannot list the inviting workspace's pairs
const REMEMBER = "axor-pair-links";
function remembered(): { token: string; label: string }[] {
  try {
    return JSON.parse(localStorage.getItem(REMEMBER) ?? "[]");
  } catch {
    return [];
  }
}
function remember(token: string, labelText: string): void {
  const list = remembered().filter((r) => r.token !== token);
  localStorage.setItem(REMEMBER, JSON.stringify([{ token, label: labelText }, ...list].slice(0, 12)));
}

/** "403 {"detail": {"code": …, "detail": …}}" → its code and words. */
function problem(e: unknown): { status: number; code: string; words: string } {
  const msg = e instanceof Error ? e.message : String(e);
  const status = Number(msg.slice(0, 3)) || 0;
  try {
    const body = JSON.parse(msg.slice(4));
    const d = body.detail ?? body;
    if (typeof d === "string") return { status, code: "", words: d };
    return { status, code: String(d.code ?? d.error ?? ""), words: String(d.detail ?? d.message ?? msg) };
  } catch {
    return { status, code: "", words: msg };
  }
}

export default function Pair({ token, withToken, node }: { token?: string; withToken?: string; node?: string }) {
  return token ? <PairPage token={token} withToken={withToken} /> : <PairHome node={node} />;
}

// ── starting one ─────────────────────────────────────────────────────────────

function PairHome({ node }: { node?: string }) {
  const qc = useQueryClient();
  const nodes = useQuery({ queryKey: ["nodes"], queryFn: api.nodes });
  const pairs = useQuery({ queryKey: ["pairs"], queryFn: api.pairs, refetchInterval: 10000 });
  const [outside, setOutside] = useState(false);
  const [paste, setPaste] = useState("");
  const open = useMutation({
    mutationFn: (nodeId: string) => api.openPair(nodeId, outside),
    onSuccess: (r) => { void qc.invalidateQueries({ queryKey: ["pairs"] }); navigate(`pair/${r.token}`); },
  });
  const example = useMutation({
    mutationFn: api.seedPair,
    onSuccess: (r) => { void qc.invalidateQueries({ queryKey: ["pairs"] }); navigate(`pair/${r.token}`); },
  });
  const sessions = (nodes.data ?? []).filter((n) => n.profile?.kind === "coding");
  const err = open.error ? problem(open.error) : null;
  const join = () => {
    const m = paste.trim().match(/pair\/([A-Za-z0-9_-]+)/) ?? paste.trim().match(/^([A-Za-z0-9_-]{16,})$/);
    if (m) navigate(`pair/${m[1]}`);
  };
  return (
    <div data-testid="pair-home" style={{ maxWidth: 860, margin: "0 auto" }}>
      <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 6px" }}>Pair sessions</h1>
      <div style={{ fontSize: 14, color: C.mut, lineHeight: 1.5, marginBottom: 22 }}>
        Send a link — they watch your agent live and can leave it a note.
        <HowItWorks>
          <li>Every step as it happens: what it ran, what failed, what it said.</li>
          <li>A note reaches the agent at its next step.</li>
          <li>Two sessions side by side, if you both drive one.</li>
        </HowItWorks>
      </div>

      <section className="mb-8">
        <div style={label}>PAIR ON A SESSION</div>
        <label className="flex items-start gap-2 mb-3" style={{ fontSize: 13, color: C.mut, lineHeight: 1.5, cursor: "pointer" }}>
          <input type="checkbox" checked={outside} onChange={(e) => setOutside(e.target.checked)}
            data-testid="pair-outside" style={{ marginTop: 3 }} />
          <span>
            people from outside the team can join too — anyone with an account, a free one too; at most
            three. <span style={{ color: C.dim }}>Paid plan. Teammates can always join.</span>
          </span>
        </label>
        {err && (
          <div data-testid="pair-error" className="mb-3" style={{ fontFamily: MONO, fontSize: 11.5, color: err.status === 402 ? C.amber : C.red, lineHeight: 1.6 }}>
            {err.words}
            {err.status === 402 && <> · <button onClick={() => navigate("pricing")} style={link}>see plans →</button></>}
          </div>
        )}
        {sessions.length === 0 ? (
          <div style={{ ...panel, padding: 16, fontSize: 13, color: C.mut }}>
            No coding sessions on this plane yet — they appear here once an agent with the axor hooks runs.{" "}
            <button onClick={() => example.mutate()} data-testid="pair-example" style={{ ...link, fontSize: 13 }}>
              {example.isPending ? "loading…" : "see an example pair →"}
            </button>
          </div>
        ) : (
          <div style={panel}>
            {sessions.map((n, i) => (
              <div key={n.node_id} data-testid={`pair-session-${n.node_id}`} className="flex items-center gap-3 px-4 py-2 flex-wrap"
                style={{ borderTop: i ? `1px solid ${C.line}` : "none",
                         background: node === n.node_id ? "rgba(127,168,204,0.08)" : "none" }}>
                <span style={{ fontSize: 13, color: C.text, flex: 1, minWidth: 200 }}>
                  {n.profile?.title || short(n.node_id)}
                  {n.profile?.title && <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}> · {short(n.node_id)}</span>}
                </span>
                <span style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>{n.profile?.agent}</span>
                <button onClick={() => open.mutate(n.node_id)} disabled={open.isPending}
                  data-testid={`pair-start-${n.node_id}`} style={btn({ color: C.text, fontSize: 11, padding: "4px 12px" })}>
                  start pairing
                </button>
              </div>
            ))}
          </div>
        )}
      </section>

      {(pairs.data?.pairs.length ?? 0) > 0 && (
        <section className="mb-8" data-testid="pair-open">
          <div style={label}>OPEN NOW</div>
          <div style={panel}>
            {pairs.data!.pairs.map((p, i) => (
              <div key={p.token} className="flex items-center gap-3 px-4 py-2 flex-wrap"
                style={{ borderTop: i ? `1px solid ${C.line}` : "none", fontFamily: MONO, fontSize: 11.5 }}>
                <button onClick={() => navigate(`pair/${p.token}`)} style={{ ...link, fontSize: 12, color: C.text }}>
                  {short(p.node_id)}
                </button>
                <span style={{ color: C.dim, flex: 1 }}>
                  {p.created_by ? `by ${p.created_by} · ` : ""}until {clock(p.expires_ts)}
                  {p.outside ? ` · open to outside the team${p.guests.length ? ` (${p.guests.length} joined)` : ""}` : " · team only"}
                </span>
                <CopyLink token={p.token} />
                <button onClick={() => void api.closePair(p.token).then(() => qc.invalidateQueries({ queryKey: ["pairs"] }))}
                  style={{ ...link, color: C.mut }}>end</button>
              </div>
            ))}
          </div>
        </section>
      )}

      <section>
        <div style={label}>JOIN WITH A LINK</div>
        <div className="flex gap-2 items-center flex-wrap">
          <input value={paste} onChange={(e) => setPaste(e.target.value)} onKeyDown={(e) => e.key === "Enter" && join()}
            placeholder="the pair link you were sent ⏎" aria-label="pair link"
            style={{ width: 380, maxWidth: "100%", background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4,
                     color: C.text, fontFamily: MONO, fontSize: 11.5, padding: "6px 8px", outline: "none" }} />
          <button onClick={join} disabled={!paste.trim()} style={btn({ fontSize: 11, padding: "5px 10px" })}>join</button>
        </div>
      </section>
    </div>
  );
}

function CopyLink({ token }: { token: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button onClick={() => { void navigator.clipboard?.writeText(pairUrl(token)); setCopied(true); }}
      data-testid="pair-copy" style={{ ...link, display: "inline-flex", alignItems: "center", gap: 4 }}>
      {copied ? <Check size={11} /> : <Copy size={11} />} {copied ? "copied" : "copy link"}
    </button>
  );
}

// ── one pair, or two side by side ────────────────────────────────────────────

function PairPage({ token, withToken }: { token: string; withToken?: string }) {
  const [picking, setPicking] = useState(false);
  const pairs = useQuery({ queryKey: ["pairs"], queryFn: api.pairs, retry: false, enabled: picking });
  const others = [
    ...(pairs.data?.pairs ?? []).map((p) => ({ token: p.token, label: short(p.node_id) })),
    ...remembered(),
  ].filter((o, i, all) => o.token !== token && all.findIndex((x) => x.token === o.token) === i);
  return (
    <div style={{ maxWidth: withToken ? 1400 : 900, margin: "0 auto" }}>
      <div className="flex items-center gap-3 mb-4 flex-wrap">
        <button onClick={() => navigate("pair")} style={btn({ fontSize: 11, padding: "4px 10px" })}>← pair sessions</button>
        <span style={{ flex: 1 }} />
        {withToken ? (
          <button onClick={() => navigate(`pair/${token}`)} style={link}>close the second session</button>
        ) : (
          <button onClick={() => setPicking(!picking)} data-testid="pair-split" style={link}>
            + another session side by side
          </button>
        )}
      </div>
      {picking && !withToken && (
        <div data-testid="pair-pick" className="mb-4 px-4 py-3" style={panel}>
          {others.length === 0 ? (
            <span style={{ fontSize: 12.5, color: C.mut }}>No other pair open that you can see — start one on another session, or open its link once.</span>
          ) : others.map((o) => (
            <button key={o.token} onClick={() => navigate(`pair/${token}`, { with: o.token })}
              style={{ ...link, display: "block", padding: "3px 0", fontSize: 12 }}>{o.label} →</button>
          ))}
        </div>
      )}
      <div style={{ display: "grid", gap: 16, gridTemplateColumns: withToken ? "repeat(auto-fit, minmax(min(380px, 100%), 1fr))" : "minmax(0, 1fr)" }}>
        <PairPane token={token} />
        {withToken && <PairPane token={withToken} />}
      </div>
    </div>
  );
}

const VERB: Record<string, string> = {
  Bash: "ran", shell: "ran", exec_command: "ran", Read: "read", Edit: "edited", MultiEdit: "edited",
  Write: "wrote", WebFetch: "fetched", WebSearch: "searched the web for", Grep: "searched", Glob: "listed",
  Skill: "used the skill", Task: "started a subagent:",
};

type Item = { ts: string; step?: PairStep; note?: PairNote };

function StepLine({ s }: { s: PairStep }) {
  if (s.kind === "task") {
    return <span style={{ color: C.text }}>asked: “{s.text}”</span>;
  }
  if (s.kind === "claim") {
    return <span style={{ color: C.text }}>said: <span style={{ color: C.mut }}>“{s.text}”</span></span>;
  }
  if (s.kind === "tool_call") {
    if (s.refused) {
      return <span style={{ color: C.red }}>refused: {s.tool} {s.target && <code>{s.target}</code>} — {s.reason?.replace(/^axor: /, "")}</span>;
    }
    return (
      <span style={{ color: C.mut }}>
        {VERB[s.tool ?? ""] ?? s.tool} {s.target && <code style={{ color: C.text }}>{s.target}</code>}
      </span>
    );
  }
  if (s.check) {
    return (
      <span style={{ color: s.check.passed ? C.green : C.red }}>
        {s.check.kind} {s.check.passed ? "passed" : "failed"}{s.check.summary ? ` — ${s.check.summary}` : ""}
      </span>
    );
  }
  return <span style={{ color: C.red }}>failed: {s.preview}</span>;
}

function PairPane({ token }: { token: string }) {
  const qc = useQueryClient();
  const feed = useQuery({ queryKey: ["pair", token], queryFn: () => api.pairFeed(token), refetchInterval: 2000, retry: false });
  const [text, setText] = useState("");
  const send = useMutation({
    mutationFn: () => api.sendNote(token, text.trim()),
    onSuccess: () => { setText(""); void qc.invalidateQueries({ queryKey: ["pair", token] }); },
  });
  const command = useMutation({
    mutationFn: (f: PairFeed) => api.command(f.pair.node_id, f.pair.desired_version + 1, { paused: !f.pair.paused }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["pair", token] }),
  });
  const end = useMutation({
    mutationFn: () => api.closePair(token),
    onSuccess: () => navigate("pair"),
  });
  const box = useRef<HTMLDivElement>(null);
  const items: Item[] = [
    ...(feed.data?.steps ?? []).filter((s) => s.kind !== "tool_result" || s.failed || s.check)
      .map((s) => ({ ts: s.ts, step: s })),
    ...(feed.data?.notes ?? []).map((n) => ({ ts: n.ts, note: n })),
  ].sort((a, b) => a.ts.localeCompare(b.ts));
  useEffect(() => {
    if (box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [items.length]);
  useEffect(() => {
    if (feed.data) remember(token, feed.data.pair.title || `${short(feed.data.pair.node_id)} · ${feed.data.pair.agent}`);
  }, [feed.data, token]);

  if (feed.error) {
    const p = problem(feed.error);
    const what: Record<string, string> = {
      team_only: "This pair session is for the team only.",
      account_required: "Sign in to join a pair session — a free account is enough.",
      pair_full: "This pair session already has three guests from outside the team.",
      plan_required: "The inviting team's plan doesn't let people from outside the team join.",
    };
    return (
      <div data-testid="pair-denied" style={{ ...panel, padding: 18 }}>
        <div style={{ fontSize: 15, fontWeight: 600, color: C.text, marginBottom: 6 }}>
          {what[p.code] ?? (p.status === 404 ? "This pair session has ended, or the link is wrong." : "Can't open this pair session.")}
        </div>
        <div style={{ fontSize: 13, color: C.mut, lineHeight: 1.6 }}>{p.words}</div>
        {p.code === "account_required" && (
          <button onClick={() => navigate("settings")} style={{ ...btn({ color: C.text }), marginTop: 12 }}>sign in →</button>
        )}
      </div>
    );
  }
  if (!feed.data) return <div style={{ fontFamily: MONO, fontSize: 12, color: C.dim }}>loading…</div>;
  const f = feed.data;
  const teammate = f.pair.you === "teammate";
  return (
    <section data-testid="pair-pane" style={{ ...panel, display: "flex", flexDirection: "column", minWidth: 0 }}>
      <div className="px-4 pt-3 pb-2" style={{ borderBottom: `1px solid ${C.line}` }}>
        <div className="flex items-center gap-2 flex-wrap">
          <span style={{ width: 7, height: 7, borderRadius: 4, background: f.pair.stopped ? C.red : f.pair.paused ? C.amber : C.green }} />
          <span style={{ fontFamily: MONO, fontSize: 12.5, color: C.text }}>{f.pair.agent || "agent"}</span>
          {f.pair.title && <span data-testid="pair-title" style={{ fontSize: 13, color: C.text }}>“{f.pair.title}”</span>}
          <span style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>{short(f.pair.node_id)}</span>
          <span style={{ flex: 1 }} />
          {f.pair.paused && <span style={{ fontFamily: MONO, fontSize: 11, color: C.amber }}>held</span>}
          {teammate && !f.pair.stopped && (
            <button onClick={() => command.mutate(f)} disabled={command.isPending} data-testid="pair-hold"
              style={btn({ fontSize: 10.5, padding: "3px 9px" })}>{f.pair.paused ? "let it continue" : "hold the agent"}</button>
          )}
          {teammate && <CopyLink token={token} />}
          {teammate && <button onClick={() => end.mutate()} style={{ ...link, color: C.mut }}>end pairing</button>}
        </div>
        <div data-testid="pair-who" style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 4, lineHeight: 1.6 }}>
          {f.pair.created_by ? `started by ${f.pair.created_by} · ` : ""}
          {teammate ? "you're here as a teammate" : "you're here as a guest from outside the team"}
          {f.pair.guests.length ? ` · from outside: ${f.pair.guests.join(", ")}` : f.pair.outside ? " · open to outside the team" : " · team only"}
          {` · until ${clock(f.pair.expires_ts)}`}
        </div>
      </div>

      <div ref={box} data-testid="pair-feed" className="px-4 py-3" style={{ height: 420, overflowY: "auto", fontSize: 12.5, lineHeight: 1.55 }}>
        {items.length === 0 && <div style={{ color: C.dim, fontFamily: MONO, fontSize: 11.5 }}>nothing yet — steps appear here as the agent works</div>}
        {items.map((it) => it.step ? (
          <div key={`s${it.step.id}`} className="flex gap-3" style={{ padding: "3px 0" }}>
            <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, width: 60, flexShrink: 0, whiteSpace: "nowrap", paddingTop: 2 }}>{clock(it.ts)}</span>
            <span style={{ minWidth: 0, overflowWrap: "anywhere" }}><StepLine s={it.step} /></span>
          </div>
        ) : (
          <div key={`n${it.note!.id}`} data-testid="pair-note" className="my-2 px-3 py-2"
            style={{ borderLeft: `2px solid ${C.amber}`, background: "rgba(242,163,60,0.06)", borderRadius: "0 6px 6px 0" }}>
            <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.amber, marginBottom: 2 }}>
              note from {it.note!.by}{it.note!.outside ? " (outside the team)" : ""} · {clock(it.ts)}
            </div>
            <div style={{ color: C.text }}>{it.note!.text}</div>
            <div data-testid="pair-note-state" style={{ fontFamily: MONO, fontSize: 10.5, color: it.note!.delivered_ts ? C.green : C.dim, marginTop: 3 }}>
              {it.note!.delivered_ts
                ? `given to the agent ${it.note!.delivered_at === "prompt" ? "with the next prompt" : `after ${it.note!.delivered_at}`}, ${clock(it.note!.delivered_ts)}`
                : "waiting for the agent's next step"}
            </div>
          </div>
        ))}
      </div>

      <div className="px-4 py-3" style={{ borderTop: `1px solid ${C.line}` }}>
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={2} aria-label="note to the agent"
          onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && text.trim()) send.mutate(); }}
          placeholder={teammate ? "a note to the agent — e.g. the test needs the fake limiter from conftest" : "a suggestion to the agent"}
          style={{ width: "100%", background: C.bg, border: `1px solid ${C.line}`, borderRadius: 6, color: C.text,
                   fontFamily: "inherit", fontSize: 13, padding: "8px 10px", outline: "none", resize: "vertical" }} />
        <div className="flex items-center gap-3 mt-2 flex-wrap">
          <button onClick={() => send.mutate()} disabled={!text.trim() || send.isPending} data-testid="pair-send"
            style={btn({ color: text.trim() ? C.text : C.dim, fontSize: 11, padding: "5px 12px" })}>send to the agent</button>
          <span style={{ fontSize: 11.5, color: C.dim, lineHeight: 1.5, flex: 1, minWidth: 200 }}>
            {teammate
              ? "Reaches the agent at its next step, as if the developer said it."
              : "Reaches the agent at its next step as a suggestion from outside the team — anything it builds from it is checked like text from the web."}
          </span>
          {send.error && <span style={{ fontFamily: MONO, fontSize: 11, color: C.red }}>{problem(send.error).words}</span>}
        </div>
      </div>
    </section>
  );
}
