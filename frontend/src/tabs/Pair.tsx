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
import { api, PairControl, PairFeed, PairNote, PairStep, streamPair } from "../api";
import { navigate } from "../router";
import { C, MONO, btn } from "../theme";
import { HowItWorks } from "../components/MachineCommand";
import { useApp } from "../store";
import { useMine } from "../team";
import { useWorkStarted } from "../workStarted";

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
  const mine = useMine();
  const email = useApp((s) => s.identityEmail);
  const nodes = useQuery({ queryKey: ["nodes"], queryFn: api.nodes });
  const fleet = useQuery({ queryKey: ["fleet"], queryFn: api.fleet, refetchInterval: 10000 });
  const pairs = useQuery({ queryKey: ["pairs"], queryFn: api.pairs, refetchInterval: 10000 });
  const requests = useQuery({ queryKey: ["pair-requests"], queryFn: api.pairRequests, refetchInterval: 10000,
                              retry: false });
  const me = useQuery({ queryKey: ["identity-me"], queryFn: api.identityMe, enabled: !!email, retry: false });
  const org = me.data?.active_org ?? "";
  const members = useQuery({ queryKey: ["org-members", org], queryFn: () => api.orgMembers(org),
                             enabled: !!org, retry: false, staleTime: 60_000 });
  const teammates = (members.data?.members ?? []).map((m) => m.email)
    .filter((e) => e.toLowerCase() !== (email || "").toLowerCase());
  const [outside, setOutside] = useState(false);
  const [invite, setInvite] = useState<Set<string>>(new Set());
  const [paste, setPaste] = useState("");
  const open = useMutation({
    mutationFn: (nodeId: string) => api.openPair(nodeId, outside, [...invite]),
    onSuccess: (r) => { void qc.invalidateQueries({ queryKey: ["pairs"] }); navigate(`pair/${r.token}`); },
  });
  const decide = useMutation({
    mutationFn: ({ id, accept }: { id: number; accept: boolean }) => api.decidePairRequest(id, accept),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ["pair-requests"] });
      void qc.invalidateQueries({ queryKey: ["pairs"] });
      if (r.token) navigate(`pair/${r.token}`);
    },
  });
  const ask = useMutation({
    mutationFn: (nodeId: string) => api.askToPair(nodeId),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["pair-requests"] }),
  });
  const started = useWorkStarted();
  const example = useMutation({
    mutationFn: api.seedPair,
    onSuccess: (r) => { void qc.invalidateQueries({ queryKey: ["pairs"] }); navigate(`pair/${r.token}`); },
  });
  // a pair is on a session that is running: a note to one that ended never lands
  const rows = new Map((fleet.data?.nodes ?? []).map((r) => [r.node_id, r] as const));
  const running = (nodes.data ?? []).filter((n) => n.profile?.kind === "coding"
    && (n.example || rows.get(n.node_id)?.active_run) && n.desired?.state.stopped !== true)
    .sort((a, b) => String(rows.get(b.node_id)?.last_ts ?? "").localeCompare(String(rows.get(a.node_id)?.last_ts ?? "")));
  const yours = running.filter((n) => mine(n.profile?.developer));
  const theirs = running.filter((n) => !mine(n.profile?.developer));
  const anySessions = (nodes.data ?? []).some((n) => n.profile?.kind === "coding");
  const asked = new Map((requests.data?.mine ?? []).map((r) => [r.node_id, r]));
  const toMe = requests.data?.to_me ?? [];
  const openMine = (pairs.data?.pairs ?? []).filter((p) => p.yours);
  const invitedTo = (pairs.data?.pairs ?? []).filter((p) => !p.yours);
  const titleOf = (id: string) => (nodes.data ?? []).find((n) => n.node_id === id)?.profile?.title || short(id);
  const err = (open.error ?? ask.error ?? decide.error) ? problem(open.error ?? ask.error ?? decide.error) : null;
  const join = () => {
    const m = paste.trim().match(/pair\/([A-Za-z0-9_-]+)/) ?? paste.trim().match(/^([A-Za-z0-9_-]{16,})$/);
    if (m) navigate(`pair/${m[1]}`);
  };
  const row = (i: number): React.CSSProperties => ({ borderTop: i ? `1px solid ${C.line}` : "none" });
  return (
    <div data-testid="pair-home" style={{ maxWidth: 860, margin: "0 auto" }}>
      <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 6px" }}>Pair sessions</h1>
      <div style={{ fontSize: 14, color: C.mut, lineHeight: 1.5, marginBottom: 22 }}>
        Invite someone to watch your agent live and leave it a note. Only by invitation: a teammate
        asks, you let them in.
        <HowItWorks>
          <li>Every step as it happens: what it ran, what failed, what it said.</li>
          <li>A note reaches the agent at its next step — and you see it in your agent too.</li>
          <li>You're told in your agent when someone joins or asks to.</li>
          <li>Only you hold your agent; whoever you let in watches and writes notes.</li>
        </HowItWorks>
      </div>

      {err && (
        <div data-testid="pair-error" className="mb-3" style={{ fontFamily: MONO, fontSize: 11.5, color: err.status === 402 ? C.amber : C.red, lineHeight: 1.6 }}>
          {err.words}
          {err.status === 402 && <> · <button onClick={() => navigate("pricing")} style={link}>see plans →</button></>}
        </div>
      )}

      {toMe.length > 0 && (
        <section className="mb-8" data-testid="pair-requests">
          <div style={label}>ASKING TO PAIR WITH YOU</div>
          <div style={panel}>
            {toMe.map((r, i) => (
              <div key={r.id} data-testid={`pair-request-${r.id}`} className="flex items-center gap-3 px-4 py-2 flex-wrap" style={row(i)}>
                <span style={{ fontSize: 13, color: C.text, flex: 1, minWidth: 220 }}>
                  {r.by} <span style={{ color: C.mut }}>asks to watch “{titleOf(r.node_id)}”</span>
                </span>
                <button onClick={() => decide.mutate({ id: r.id, accept: true })} disabled={decide.isPending}
                  data-testid={`pair-let-in-${r.id}`} style={btn({ color: C.green, borderColor: C.green, fontSize: 11, padding: "4px 12px" })}>
                  let them in
                </button>
                <button onClick={() => decide.mutate({ id: r.id, accept: false })} disabled={decide.isPending}
                  style={{ ...link, color: C.mut }}>not now</button>
              </div>
            ))}
          </div>
        </section>
      )}

      <section className="mb-8">
        <div style={label}>PAIR ON YOUR SESSION</div>
        {yours.length === 0 ? (
          <div style={{ ...panel, padding: 16, fontSize: 13, color: C.mut }}>
            None of your sessions is running now — start your agent, and it appears here.
            {!anySessions && !started && <>{" "}
              <button onClick={() => example.mutate()} data-testid="pair-example" style={{ ...link, fontSize: 13 }}>
                {example.isPending ? "loading…" : "see an example pair →"}
              </button></>}
          </div>
        ) : (
          <>
            {teammates.length > 0 && (
              <div className="flex items-center gap-2 mb-3 flex-wrap" data-testid="pair-invite" style={{ fontSize: 13, color: C.mut }}>
                <span>invite</span>
                {teammates.map((t) => {
                  const on = invite.has(t);
                  return (
                    <button key={t} onClick={() => setInvite((cur) => { const n = new Set(cur); if (on) n.delete(t); else n.add(t); return n; })}
                      aria-pressed={on} style={{ ...btn({ fontSize: 11, padding: "3px 10px" }),
                        color: on ? C.steel : C.mut, borderColor: on ? C.steel : C.line }}>{t}</button>
                  );
                })}
                <span style={{ color: C.dim, fontSize: 12 }}>— or just send the link</span>
              </div>
            )}
            <label className="flex items-start gap-2 mb-3" style={{ fontSize: 13, color: C.mut, lineHeight: 1.5, cursor: "pointer" }}>
              <input type="checkbox" checked={outside} onChange={(e) => setOutside(e.target.checked)}
                data-testid="pair-outside" style={{ marginTop: 3 }} />
              <span>
                people from outside the team can join with the link too — their own account, a free one
                too; at most three. <span style={{ color: C.dim }}>Paid plan.</span>
              </span>
            </label>
            <div style={panel}>
              {yours.map((n, i) => (
                <div key={n.node_id} data-testid={`pair-session-${n.node_id}`} className="flex items-center gap-3 px-4 py-2 flex-wrap"
                  style={{ ...row(i), background: node === n.node_id ? "rgba(127,168,204,0.08)" : "none" }}>
                  {(() => {
                    const r = rows.get(n.node_id);
                    const last = r?.last_ts ? Date.now() - new Date(r.last_ts).getTime() : Infinity;
                    const now = !!r?.active_run && last < 120_000;
                    return (
                      <>
                        <span style={{ width: 7, height: 7, borderRadius: 4, background: now ? C.green : C.dim }} />
                        <span style={{ fontSize: 13, color: C.text, flex: 1, minWidth: 200 }}>
                          {n.profile?.title || short(n.node_id)}
                          {n.profile?.title && <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}> · {short(n.node_id)}</span>}
                        </span>
                        <span data-testid={`pair-activity-${n.node_id}`} style={{ fontFamily: MONO, fontSize: 10.5, color: now ? C.green : C.dim }}>
                          {now ? `working now${r?.current_tool ? ` · ${r.current_tool}` : ""}`
                            : r?.last_ts ? `last step ${Math.round(last / 60000)} min ago` : "no steps yet"}
                        </span>
                      </>
                    );
                  })()}
                  <span style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>{n.profile?.agent}</span>
                  <button onClick={() => open.mutate(n.node_id)} disabled={open.isPending}
                    data-testid={`pair-start-${n.node_id}`} style={btn({ color: C.text, fontSize: 11, padding: "4px 12px" })}>
                    {invite.size ? `start pairing · invite ${invite.size}` : "start pairing"}
                  </button>
                </div>
              ))}
            </div>
          </>
        )}
      </section>

      {openMine.length > 0 && (
        <section className="mb-8" data-testid="pair-open">
          <div style={label}>YOUR OPEN PAIRS</div>
          <div style={panel}>
            {openMine.map((p, i) => (
              <div key={p.token} className="flex items-center gap-3 px-4 py-2 flex-wrap"
                style={{ ...row(i), fontFamily: MONO, fontSize: 11.5 }}>
                <button onClick={() => navigate(`pair/${p.token}`)} style={{ ...link, fontSize: 12, color: C.text }}>
                  {p.title || short(p.node_id)}
                </button>
                <span style={{ color: C.dim, flex: 1 }}>
                  until {clock(p.expires_ts)}
                  {p.invited.length ? ` · invited ${p.invited.join(", ")}` : ""}
                  {p.guests.length ? ` · joined ${p.guests.map((g) => g.who).join(", ")}` : ""}
                  {p.outside ? " · open to outside the team" : ""}
                </span>
                <CopyLink token={p.token} />
                <button onClick={() => void api.closePair(p.token).then(() => qc.invalidateQueries({ queryKey: ["pairs"] }))}
                  style={{ ...link, color: C.mut }}>end</button>
              </div>
            ))}
          </div>
        </section>
      )}

      {invitedTo.length > 0 && (
        <section className="mb-8" data-testid="pair-invited">
          <div style={label}>YOU'RE INVITED</div>
          <div style={panel}>
            {invitedTo.map((p, i) => (
              <div key={p.token} className="flex items-center gap-3 px-4 py-2 flex-wrap" style={row(i)}>
                <span style={{ fontSize: 13, color: C.text, flex: 1, minWidth: 220 }}>
                  {p.title || short(p.node_id)} <span style={{ color: C.mut }}>· {p.created_by}'s session</span>
                </span>
                <button onClick={() => navigate(`pair/${p.token}`)} data-testid={`pair-join-${p.node_id}`}
                  style={btn({ color: C.text, fontSize: 11, padding: "4px 12px" })}>join</button>
              </div>
            ))}
          </div>
        </section>
      )}

      {theirs.length > 0 && (
        <section className="mb-8" data-testid="pair-teammates">
          <div style={label}>TEAMMATES' SESSIONS RUNNING NOW</div>
          <div style={panel}>
            {theirs.map((n, i) => {
              const r = asked.get(n.node_id);
              return (
                <div key={n.node_id} className="flex items-center gap-3 px-4 py-2 flex-wrap" style={row(i)}>
                  <span style={{ fontSize: 13, color: C.text, flex: 1, minWidth: 220 }}>
                    {n.profile?.title || short(n.node_id)} <span style={{ color: C.mut }}>· {n.profile?.developer}</span>
                  </span>
                  {r?.status === "accepted" && r.token ? (
                    <button onClick={() => navigate(`pair/${r.token}`)} style={btn({ color: C.green, borderColor: C.green, fontSize: 11, padding: "4px 12px" })}>
                      let in — join
                    </button>
                  ) : r?.status === "pending" ? (
                    <span data-testid={`pair-asked-${n.node_id}`} style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>asked — {n.profile?.developer} is told in their agent</span>
                  ) : (
                    <button onClick={() => ask.mutate(n.node_id)} disabled={ask.isPending}
                      data-testid={`pair-ask-${n.node_id}`} style={btn({ fontSize: 11, padding: "4px 12px" })}>
                      {r?.status === "declined" ? "ask again" : "ask to pair"}
                    </button>
                  )}
                </div>
              );
            })}
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
    ...(pairs.data?.pairs ?? []).map((p) => ({ token: p.token,
      label: p.title ? `${p.title} · ${short(p.node_id)}` : short(p.node_id) })),
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

type Item = { ts: string; step?: PairStep; note?: PairNote; control?: PairControl; newTurn?: boolean };

// The lines an edit changed, with the lines around them it kept as context:
// what is the same at the start and at the end is said once, not removed and
// added again.
function lineDiff(oldText: string, newText: string): { sign: " " | "-" | "+"; text: string }[] {
  const a = oldText ? oldText.split("\n") : [];
  const b = newText.split("\n");
  let head = 0;
  while (head < a.length && head < b.length && a[head] === b[head]) head++;
  let tail = 0;
  while (tail < a.length - head && tail < b.length - head
         && a[a.length - 1 - tail] === b[b.length - 1 - tail]) tail++;
  return [
    ...a.slice(0, head).map((t) => ({ sign: " " as const, text: t })),
    ...a.slice(head, a.length - tail).map((t) => ({ sign: "-" as const, text: t })),
    ...b.slice(head, b.length - tail).map((t) => ({ sign: "+" as const, text: t })),
    ...a.slice(a.length - tail).map((t) => ({ sign: " " as const, text: t })),
  ];
}

function Diff({ d }: { d: NonNullable<PairStep["diff"]> }) {
  return (
    <div data-testid="pair-diff" style={{ fontFamily: MONO, fontSize: 11, background: C.bg, border: `1px solid ${C.line}`,
      borderRadius: 5, padding: "4px 8px", margin: "3px 0 2px", maxHeight: 220, overflow: "auto", whiteSpace: "pre-wrap" }}>
      {d.patch
        ? d.patch.split("\n").map((t, i) => (
            <div key={i} style={{ color: t.startsWith("+") ? C.green : t.startsWith("-") ? C.red : C.dim }}>{t}</div>))
        : (d.hunks ?? []).map((h, i) => (
            <div key={i} style={{ marginBottom: 4 }}>
              {lineDiff(h.old, h.new).map((l, j) => (
                <div key={j} style={{ color: l.sign === "-" ? C.red : l.sign === "+" ? C.green : C.dim }}>
                  {l.sign} {l.text}
                </div>))}
            </div>))}
    </div>
  );
}

function StepLine({ s }: { s: PairStep }) {
  if (s.kind === "said") {
    return s.role === "developer"
      ? <span data-testid="pair-asked" style={{ color: C.text }}>asked: “{s.text}”</span>
      : <span data-testid="pair-agent-said" style={{ color: C.mut, whiteSpace: "pre-wrap" }}>{s.text}</span>;
  }
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
        {s.diff && <Diff d={s.diff} />}
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
  // the first look (who, what session, the last steps), then live: the stream
  // brings each step, the notes and the hold as they happen
  const feed = useQuery({ queryKey: ["pair", token], queryFn: () => api.pairFeed(token), refetchInterval: 30000, retry: false });
  const [liveSteps, setLiveSteps] = useState<PairStep[]>([]);
  const [liveNotes, setLiveNotes] = useState<PairNote[] | null>(null);
  const [livePair, setLivePair] = useState<{ paused: boolean; stopped: boolean; desired_version: number; confirmed: boolean } | null>(null);
  const [liveControls, setLiveControls] = useState<PairControl[]>([]);
  const opened = feed.data !== undefined;
  useEffect(() => {
    if (!opened) return;
    setLiveSteps([]);
    return streamPair(token, feed.data?.cursor ?? 0, {
      step: (st) => setLiveSteps((cur) => (cur.some((x) => x.id === st.id) ? cur : [...cur, st])),
      notes: setLiveNotes,
      pair: setLivePair,
      control: (c) => setLiveControls((cur) => [...cur, c]),
      ended: () => void qc.invalidateQueries({ queryKey: ["pair", token] }),
    });
    // the stream opens once, from the first look's cursor
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, opened]);
  const [text, setText] = useState("");
  const send = useMutation({
    mutationFn: () => api.sendNote(token, text.trim(), feed.data?.pair.you === "teammate" ? feed.data.pair.node_id : undefined),
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
  const seen = new Set((feed.data?.steps ?? []).map((st) => st.id));
  const steps = [...(feed.data?.steps ?? []), ...liveSteps.filter((st) => !seen.has(st.id))];
  const items: Item[] = [
    ...steps.filter((s) => s.kind !== "tool_result" || s.failed || s.check)
      .map((s) => ({ ts: s.ts, step: s })),
    // a note sits where the agent was given it; until then, where it was sent
    ...(liveNotes ?? feed.data?.notes ?? []).map((n) => ({ ts: n.delivered_ts ?? n.ts, note: n })),
    ...[...new Map([...(feed.data?.controls ?? []), ...liveControls]
      .map((c) => [`${c.ts}|${c.act}`, c] as const)).values()].map((c) => ({ ts: c.ts, control: c })),
  ].sort((a, b) => a.ts.localeCompare(b.ts));
  // a turn (one run of the agent) starts where the run changes
  let prevRun: string | undefined;
  for (const it of items) {
    if (!it.step?.run) continue;
    it.newTurn = prevRun !== undefined && it.step.run !== prevRun;
    prevRun = it.step.run;
  }
  // what the agent is working on: the developer's latest request
  const asked = [...steps].reverse().find((st) => st.kind === "said" && st.role === "developer")?.text;
  // an answer right after a check that failed — said beside the answer
  const contradicted = new Set<number>();
  let lastCheckFailed = false;
  for (const st of steps) {
    if (st.check) lastCheckFailed = !st.check.passed;
    else if (st.kind === "said" && st.role === "developer") lastCheckFailed = false;
    else if (st.kind === "claim" && lastCheckFailed) contradicted.add(st.id);
  }
  const decide = useMutation({
    mutationFn: ({ id, send: ok }: { id: number; send: boolean }) =>
      api.decideAgentMessage(feed.data!.pair.node_id, id, ok),
  });
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
  const f = livePair ? { ...feed.data, pair: { ...feed.data.pair, ...livePair } } : feed.data;
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
          <span data-testid="pair-state" style={{ fontFamily: MONO, fontSize: 11,
            color: f.pair.stopped ? C.red : f.pair.paused ? C.amber : f.pair.confirmed === false ? C.dim : C.green }}>
            {command.isPending ? (f.pair.paused ? "resuming…" : "pausing…")
              : f.pair.stopped ? "stopped"
              : f.pair.paused
                ? (f.pair.confirmed === false ? "paused — waiting for the agent to stop" : "paused — the agent is waiting")
                : (f.pair.confirmed === false ? "resumed — waiting for the agent" : "running")}
          </span>
          {teammate && f.pair.yours !== false && !f.pair.stopped && (
            <button onClick={() => command.mutate(f)} disabled={command.isPending} data-testid="pair-hold"
              style={btn({ fontSize: 10.5, padding: "3px 9px" })}>{f.pair.paused ? "resume the agent" : "pause the agent"}</button>
          )}
          {teammate && f.pair.yours !== false && <CopyLink token={token} />}
          {teammate && f.pair.yours !== false && <button onClick={() => end.mutate()} style={{ ...link, color: C.mut }}>end pairing</button>}
        </div>
        <div data-testid="pair-who" style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 4, lineHeight: 1.6 }}>
          {f.pair.yours === false
            ? `${f.pair.created_by}'s session · you're here at their invitation`
            : <>{f.pair.created_by ? `your session · ` : ""}{teammate ? "only you can pause or stop it" : "you're here as a guest from outside the team"}</>}
          {f.pair.joined?.length ? ` · watching: ${f.pair.joined.join(", ")}` : ""}
          {f.pair.guests.length ? ` · from outside: ${f.pair.guests.join(", ")}` : f.pair.outside ? " · open to outside the team" : " · team only"}
          {` · link open until ${clock(f.pair.expires_ts)}`}
        </div>
        {(asked || f.pair.title) && (
          <div data-testid="pair-task" style={{ fontSize: 13, color: C.text, marginTop: 6, lineHeight: 1.5 }}>
            <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>working on </span>“{asked ?? f.pair.title}”
          </div>
        )}
      </div>

      <div ref={box} data-testid="pair-feed" className="px-4 py-3" style={{ height: 420, overflowY: "auto", fontSize: 12.5, lineHeight: 1.55 }}>
        {items.length === 0 && <div style={{ color: C.dim, fontFamily: MONO, fontSize: 11.5 }}>nothing yet — steps appear here as the agent works</div>}
        {items.map((it) => it.control ? (
          <div key={`c${it.control.ts}${it.control.act}`} data-testid="pair-control" className="my-1"
            style={{ fontFamily: MONO, fontSize: 10.5, color: C.amber, borderTop: `1px dashed ${C.line}`, paddingTop: 3 }}>
            {clock(it.ts)} · {it.control.act}{it.control.by ? ` by ${it.control.by}` : ""}
          </div>
        ) : it.step ? (
          <div key={`s${it.step.id}`}>
          {it.newTurn && (
            <div data-testid="pair-turn" style={{ fontFamily: MONO, fontSize: 10, color: C.dim,
              borderTop: `1px solid ${C.line}`, margin: "8px 0 4px", paddingTop: 3 }}>new turn · {clock(it.ts)}</div>
          )}
          <div className="flex gap-3" style={{ padding: "3px 0" }}>
            <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, width: 60, flexShrink: 0, whiteSpace: "nowrap", paddingTop: 2 }}>{clock(it.ts)}</span>
            <span style={{ minWidth: 0, overflowWrap: "anywhere" }}>
              <StepLine s={it.step} />
              {contradicted.has(it.step.id) && (
                <div data-testid="pair-contradiction" style={{ color: C.red, fontFamily: MONO, fontSize: 10.5 }}>
                  ⚠ the last check before this answer failed
                </div>
              )}
            </span>
          </div>
          </div>
        ) : (
          <div key={`n${it.note!.id}`} data-testid="pair-note" className="my-2"
            style={{ borderLeft: `2px solid ${C.amber}`, background: "rgba(242,163,60,0.06)", borderRadius: "0 6px 6px 0",
                     padding: "8px 12px" }}>
            <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.amber, marginBottom: 2 }}>
              {it.note!.from_node
                ? <>agent → agent · {short(it.note!.from_node)} → {short(it.note!.node_id ?? "")}</>
                : <>note from {it.note!.by}{it.note!.outside ? " (outside the team)" : ""}</>} · {clock(it.ts)}
              {it.note!.taint && it.note!.taint.length > 0 && (
                <span data-testid="pair-note-taint"> · carries {it.note!.taint.join(", ")}</span>
              )}
            </div>
            <div style={{ color: C.text }}>{it.note!.text}</div>
            <div data-testid="pair-note-state" style={{ fontFamily: MONO, fontSize: 10.5, color: it.note!.delivered_ts ? C.green : C.dim, marginTop: 3 }}>
              {it.note!.held && f.pair.yours !== false && it.note!.node_id === f.pair.node_id ? (
                <span className="flex gap-2 items-center flex-wrap">
                  <span style={{ color: C.amber }}>waiting for your OK</span>
                  <button data-testid="pair-held-send" onClick={() => decide.mutate({ id: it.note!.id, send: true })}
                    style={btn({ fontSize: 10.5, padding: "1px 8px", color: C.green, borderColor: C.green })}>send to my agent</button>
                  <button data-testid="pair-held-dismiss" onClick={() => decide.mutate({ id: it.note!.id, send: false })}
                    style={btn({ fontSize: 10.5, padding: "1px 8px" })}>dismiss</button>
                </span>
              ) : it.note!.held ? "waiting for its developer's OK"
                : it.note!.dismissed_ts ? "dismissed by its developer"
                : it.note!.delivered_ts
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
