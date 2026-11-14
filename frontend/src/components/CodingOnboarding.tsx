// The coding-agent way in: Claude Code and Codex governed through their hooks.
// No tools to declare, no base URLs to repoint — one install, one command per
// repository, two environment variables, then the first session shows up here
// on its own. What a first-time visitor asks before any of that is answered at
// the top: what is free, and whether they need an account (they do when the
// plane has auth on — the sessions report into their workspace).
import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Check, Copy } from "lucide-react";
import { api } from "../api";
import { navigate } from "../router";
import { isGuestToken, useApp } from "../store";
import { C, MONO, btn } from "../theme";
import { IdentityLogin } from "../tabs/Settings";

type Agent = "claude-code" | "codex";
const AGENTS: { id: Agent; label: string; note: string }[] = [
  { id: "claude-code", label: "Claude Code", note: "adds hooks to .claude/settings.json — nothing else to do" },
  { id: "codex", label: "Codex", note: "adds hooks to .codex/hooks.json — then run /hooks in Codex once and approve them" },
];

const PLANE_URL = typeof window === "undefined" ? "" : window.location.origin;

const TREE = `task: Add rate limiting to the auth module
nodes:
  plan: {agent: claude-code, model: claude-fable-5-2, out: plan}
  read: {agent: codex, model: luna, count: 5, access: read,
         shard: plan.questions, out: findings}
  impl: {agent: claude-code, model: claude-opus-5-5, after: [read]}`;

export function isCodingSession(nodeId: string): boolean {
  return nodeId.startsWith("claude-code-") || nodeId.startsWith("codex-");
}

/** The coding example (Home → Explore example coding agents) and nothing of a
 * production fleet: the screens then talk about coding agents, not adapters. */
export function codingExampleOnly(ids: string[]): boolean {
  const example = ids.some((id) => /^(claude-code|codex)-example-/.test(id));
  const production = ids.some((id) => !isCodingSession(id) && !id.startsWith("tree-"));
  return example && !production;
}

function Block({ text, id, copied, onCopy }: {
  text: string; id: string; copied: string | null; onCopy: (id: string, text: string) => void;
}) {
  return (
    <div className="flex items-start gap-2 mb-2" style={{
      background: C.bg, border: `1px solid ${C.line}`, borderRadius: 6, padding: "8px 10px",
    }}>
      <pre data-testid={`cmd-${id}`} style={{ margin: 0, flex: 1, fontFamily: MONO, fontSize: 11.5, color: C.text, whiteSpace: "pre-wrap" }}>{text}</pre>
      <button onClick={() => onCopy(id, text)} aria-label="copy"
        style={btn({ color: C.mut, fontSize: 10, padding: "2px 6px" })}>
        {copied === id ? <Check size={11} /> : <Copy size={11} />}
      </button>
    </div>
  );
}

const step = (n: number, text: string, first = false) => (
  <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, letterSpacing: "0.06em",
                margin: first ? "0 0 6px" : "16px 0 6px" }}>{n} · {text}</div>
);
const hint = { fontFamily: MONO, fontSize: 10.5, color: C.dim, marginBottom: 4, lineHeight: 1.6 } as const;

export default function CodingOnboarding({ then }: { then?: () => void }) {
  const [agents, setAgents] = useState<Agent[]>(["claude-code"]);
  const [copied, setCopied] = useState<string | null>(null);
  const [key, setKey] = useState<string | null>(null);
  const connect = useApp((s) => s.connect);
  const email = useApp((s) => s.identityEmail);
  const token = useApp((s) => s.apiToken);
  // auth off (a local plane): nothing to sign in to, no key to make
  const auth = useQuery({ queryKey: ["auth-status"], queryFn: api.authStatus, staleTime: 60_000 });
  const authOn = auth.data?.auth_enabled ?? null;
  const signedIn = !!email || (!!token && !isGuestToken(token));
  const mint = useMutation({
    mutationFn: () => api.createKey(["ingest"], "coding agents"),
    onSuccess: (k) => setKey(k.secret),
  });
  const nodes = useQuery({ queryKey: ["nodes"], queryFn: api.nodes, refetchInterval: 3000 });
  const sessions = (nodes.data ?? []).filter((n) => isCodingSession(n.node_id));
  // the first governed session IS the connection: the header stops asking
  const mode = useApp((s) => s.connection.mode);
  useEffect(() => {
    if (sessions.length > 0 && mode === "none") connect("adapter", []);
  }, [sessions.length, mode, connect]);
  const copy = (id: string, text: string) => {
    void navigator.clipboard?.writeText(text);
    setCopied(id);
    setTimeout(() => setCopied(null), 1500);
  };
  const toggle = (a: Agent) =>
    setAgents((cur) => (cur.includes(a) ? cur.filter((x) => x !== a) : [...cur, a]));

  const needsKey = authOn !== false;
  const env = [
    `export AXOR_PLANE_URL=${PLANE_URL}`,
    needsKey ? `export AXOR_INGEST_KEY=${key ?? "<the key — create it below>"}` : null,
  ].filter(Boolean).join("\n");
  // on to the next part of "both" is not a connection: nothing reported yet
  const goAgents = () => { if (then) { then(); return; } connect("adapter", []); navigate("govern"); };

  return (
    <div data-testid="coding-onboarding">
      <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 4px" }}>Govern your coding agents</h1>
      <div style={{ fontSize: 13.5, color: C.mut, marginBottom: 10, lineHeight: 1.6 }}>
        Every tool call your agents make is checked before it runs. What Claude Code read from
        the web stays marked when Codex carries it. Each session shows up here — pause, stop or
        cap it from Agents.
      </div>
      <div data-testid="coding-free" style={{ fontFamily: MONO, fontSize: 11, color: C.mut, lineHeight: 1.7,
        border: `1px solid ${C.line}`, borderRadius: 6, padding: "8px 12px", marginBottom: 18 }}>
        {then ? (
          <>
            <span style={{ color: C.green }}>free: 1 developer, 1 agent family and 1 production agent</span>.
            <br />Team, $299 / month: 10 production agents and 5 developers — more developers $25 each.{" "}
          </>
        ) : (
          <>
            <span style={{ color: C.green }}>free for 1 developer and 1 agent</span> — Claude Code or
            Codex, with hooks, agent trees and pause / stop / cap. Any number of repositories and sessions.
            <br />both agents together (one writes, the other reviews) or a second developer: $25 per
            developer per month.{" "}
          </>
        )}
        <button onClick={() => navigate(then ? "pricing?for=both" : "pricing?for=coding")} data-testid="coding-pricing"
          style={{ background: "none", border: "none", padding: 0, cursor: "pointer",
                   fontFamily: MONO, fontSize: 11, color: C.steel }}>pricing →</button>
      </div>

      {step(1, "WHICH AGENTS", true)}
      <div className="flex gap-2 flex-wrap">
        {AGENTS.map((a) => (
          <button key={a.id} onClick={() => toggle(a.id)} data-testid={`pick-${a.id}`}
            style={btn({
              color: agents.includes(a.id) ? C.text : C.dim,
              borderColor: agents.includes(a.id) ? C.steel : C.line, fontSize: 11.5, padding: "6px 12px",
            })}>
            {agents.includes(a.id) ? "✓ " : ""}{a.label}
          </button>
        ))}
      </div>

      {agents.length > 1 && (
        <div data-testid="coding-both-note" style={{ ...hint, marginTop: 6 }}>
          both is the paid plan: on the free one the plane keeps the agent that reports first —
          the other is still protected by its hooks.
        </div>
      )}

      {step(2, "INSTALL — ONCE, ON YOUR MACHINE")}
      <Block id="install" copied={copied} onCopy={copy} text="uv tool install 'axor-wrap[plane]'" />
      <div style={hint}>no uv? <span style={{ color: C.mut }}>pipx install 'axor-wrap[plane]'</span></div>

      {step(3, "IN EACH REPOSITORY")}
      <Block id="repo" copied={copied} onCopy={copy}
        text={agents.map((a) => `axor-wrap agents install ${a}`).join("\n") || "# pick an agent above"} />
      {agents.map((a) => (
        <div key={a} style={hint}>
          {AGENTS.find((x) => x.id === a)!.label}: {AGENTS.find((x) => x.id === a)!.note}
        </div>
      ))}

      {step(4, "CONNECT THEM TO THIS WORKSPACE")}
      {needsKey && authOn && !signedIn ? (
        <div data-testid="coding-signin" style={{ border: `1px solid ${C.line}`, borderRadius: 8, padding: 14 }}>
          <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, lineHeight: 1.6, marginBottom: 10 }}>
            Your agents report into your workspace, so this step needs an account — free,
            a minute. Then the key for your agents is one click, right here.
          </div>
          <IdentityLogin initialMode="signup" />
        </div>
      ) : (
        <>
          <Block id="env" copied={copied} onCopy={copy} text={env} />
          {needsKey && !key && (
            <button onClick={() => mint.mutate()} disabled={mint.isPending} data-testid="mint-coding-key"
              style={btn({ color: C.steel, borderColor: C.steel, fontSize: 10.5, padding: "4px 10px" })}>
              {mint.isPending ? "…" : "create the key"}
            </button>
          )}
          <div style={{ ...hint, marginTop: 6 }}>
            {key ? <span style={{ color: C.amber }}>the key is shown once — copy it now. </span> : null}
            {needsKey
              ? "the key lets your agents report here and nothing else. "
              : "this plane is open — no key needed. "}
            Put these in your shell profile (~/.zshrc, ~/.bashrc) so every terminal has them.
          </div>
          {mint.isError && (
            <div style={{ ...hint, color: C.red }}>could not create a key ({(mint.error as Error).message})</div>
          )}
        </>
      )}

      {step(5, "START A SESSION")}
      <div data-testid="coding-sessions" className="p-3" style={{ background: C.panel, border: `1px solid ${sessions.length ? C.green : C.line}`, borderRadius: 8, fontFamily: MONO, fontSize: 11.5 }}>
        {sessions.length === 0 ? (
          <span style={{ color: C.mut }}>waiting for the first session — run <span style={{ color: C.text }}>claude</span> or <span style={{ color: C.text }}>codex</span> in the repository…</span>
        ) : (
          <>
            <div style={{ color: C.green, marginBottom: 6 }}>● {sessions.length} session{sessions.length === 1 ? "" : "s"} governed</div>
            {sessions.slice(0, 5).map((n) => <div key={n.node_id} style={{ color: C.mut }}>{n.node_id}</div>)}
          </>
        )}
      </div>

      <details className="mt-4">
        <summary style={{ fontFamily: MONO, fontSize: 11, color: C.steel, cursor: "pointer" }}>
          several agents on one task — a tree (planner → readers → implementer)
        </summary>
        <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, margin: "8px 0" }}>
          axor.tree.yaml, then <span style={{ color: C.text }}>axor-wrap tree run</span>. Readers run read-only,
          their findings are checked against the files they opened, and the run shows under Agents → agent trees.
        </div>
        <Block id="tree" copied={copied} onCopy={copy} text={TREE} />
      </details>

      <div className="flex gap-3 mt-5 items-center">
        {then ? (
          <button data-testid="coding-done" onClick={goAgents}
            style={btn({ color: C.text, borderColor: C.steel, fontSize: 12, padding: "7px 16px" })}>
            next: your production agent →
          </button>
        ) : sessions.length ? (
          <button data-testid="coding-done" onClick={goAgents}
            style={btn({ color: C.text, borderColor: C.green, fontSize: 12, padding: "7px 16px" })}>
            see them in Agents →
          </button>
        ) : (
          <button data-testid="coding-skip" onClick={goAgents}
            style={{ background: "none", border: "none", padding: 0, cursor: "pointer",
                     fontFamily: MONO, fontSize: 11, color: C.dim }}>
            skip — I'll start a session later →
          </button>
        )}
      </div>
    </div>
  );
}
