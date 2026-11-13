// The coding-agent way in: Claude Code and Codex governed through their hooks.
// No tools to declare, no base URLs to repoint — two commands and an environment
// variable, then the first session shows up here on its own.
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Check, Copy } from "lucide-react";
import { api } from "../api";
import { navigate } from "../router";
import { useApp } from "../store";
import { C, MONO, btn } from "../theme";

type Agent = "claude-code" | "codex";
const AGENTS: { id: Agent; label: string; note: string }[] = [
  { id: "claude-code", label: "Claude Code", note: "hooks in .claude/settings.json" },
  { id: "codex", label: "Codex", note: "hooks in .codex/hooks.json — trust them once with /hooks" },
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

export default function CodingOnboarding({ then }: { then?: () => void }) {
  const [agents, setAgents] = useState<Agent[]>(["claude-code", "codex"]);
  const [copied, setCopied] = useState<string | null>(null);
  const [key, setKey] = useState<string | null>(null);
  const connect = useApp((s) => s.connect);
  const mint = useMutation({
    mutationFn: () => api.createKey(["ingest"], "coding agents"),
    onSuccess: (k) => setKey(k.secret),
  });
  const nodes = useQuery({ queryKey: ["nodes"], queryFn: api.nodes, refetchInterval: 3000 });
  const sessions = (nodes.data ?? []).filter((n) => isCodingSession(n.node_id));
  const copy = (id: string, text: string) => {
    void navigator.clipboard?.writeText(text);
    setCopied(id);
    setTimeout(() => setCopied(null), 1500);
  };
  const toggle = (a: Agent) =>
    setAgents((cur) => (cur.includes(a) ? cur.filter((x) => x !== a) : [...cur, a]));

  const env = [`export AXOR_PLANE_URL=${PLANE_URL}`, key ? `export AXOR_INGEST_KEY=${key}` : null]
    .filter(Boolean).join("\n");

  return (
    <div data-testid="coding-onboarding">
      <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 4px" }}>Govern your coding agents</h1>
      <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, marginBottom: 16, lineHeight: 1.6 }}>
        Every tool call your agents make goes through the kernel first. One repository, one
        taint ledger: what Claude Code read from the web is tainted when Codex carries it.
        Each session shows up here; pause, stop or cap it from Agents.
      </div>

      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, letterSpacing: "0.06em", marginBottom: 6 }}>1 · WHICH AGENTS</div>
      <div className="flex gap-2 mb-4 flex-wrap">
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

      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, letterSpacing: "0.06em", marginBottom: 6 }}>2 · IN YOUR REPOSITORY</div>
      <Block id="install" copied={copied} onCopy={copy}
        text={["pip install 'axor-wrap[plane]'",
          ...agents.map((a) => `axor-wrap agents install ${a}`)].join("\n")} />
      {agents.map((a) => (
        <div key={a} style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginBottom: 4 }}>
          {AGENTS.find((x) => x.id === a)!.label}: {AGENTS.find((x) => x.id === a)!.note}
        </div>
      ))}

      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, letterSpacing: "0.06em", margin: "14px 0 6px" }}>3 · WHERE THE AGENTS RUN</div>
      <Block id="env" copied={copied} onCopy={copy} text={env} />
      {!key && (
        <button onClick={() => mint.mutate()} disabled={mint.isPending} data-testid="mint-coding-key"
          style={btn({ color: C.steel, borderColor: C.steel, fontSize: 10.5, padding: "4px 10px" })}>
          {mint.isPending ? "…" : "create an ingest key for them"}
        </button>
      )}
      {mint.isError && (
        <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, marginTop: 4 }}>
          could not create a key ({(mint.error as Error).message}) — on an open plane none is needed.
        </div>
      )}

      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, letterSpacing: "0.06em", margin: "18px 0 6px" }}>4 · START A SESSION</div>
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

      <div className="flex gap-2 mt-5">
        <button data-testid="coding-done"
          onClick={() => { connect("adapter", []); if (then) then(); else navigate("govern"); }}
          style={btn({ color: C.text, borderColor: C.steel, fontSize: 12, padding: "7px 16px" })}>
          {then ? "next: your production agent →" : sessions.length ? "see them in Agents →" : "done — go to Agents →"}
        </button>
        <button onClick={() => navigate("pricing?for=coding")}
          style={btn({ color: C.dim, fontSize: 11, padding: "7px 12px" })}>
          pricing for coding agents
        </button>
      </div>
    </div>
  );
}
