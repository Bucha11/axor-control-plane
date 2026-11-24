// The proxy as a gate (axor-proxy --guard): no change to the agent's code, any
// language — the proxy runs beside the agent, in front of its tools, and every
// tool call is decided before it reaches the tool. What it decides with is
// what this screen builds: the tools, what each one does, and which bring in
// outside text (axor.manifests.json, compiled by the same engine as the
// Config Builder's), plus tools.json for where each tool really lives.
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Check, Copy, Download, Plug } from "lucide-react";
import { api } from "../api";
import { navigate } from "../router";
import { isGuestToken, useApp, type ToolKlass } from "../store";
import { untrustedFields } from "../policyConfig";
import ScanTools from "./ScanTools";
import { C, MONO, btn } from "../theme";
import { IdentityLogin } from "../tabs/Settings";

type Klass = ToolKlass;
interface GuardTool { name: string; url: string; klass: Klass; outside: boolean }

const CLASSES: { id: Klass; label: string }[] = [
  { id: "READ", label: "reads" },
  { id: "WRITE", label: "changes something" },
  { id: "EXPORT", label: "sends out" },
  { id: "EXEC", label: "runs code" },
];

const EXAMPLE: GuardTool[] = [
  { name: "web_search", url: "https://api.search.example/v1", klass: "READ", outside: true },
  { name: "send_report", url: "https://slack.example/api/post", klass: "EXPORT", outside: false },
  { name: "run_query", url: "https://db.internal.example/query", klass: "READ", outside: false },
];

const LOCAL_PROXY = "http://localhost:8401";

function save(name: string, data: unknown): void {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
  a.download = name;
  a.click();
}

const step = (n: number, text: string) => (
  <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, letterSpacing: "0.06em", margin: "18px 0 6px" }}>
    {n} · {text}
  </div>
);
const hint = { fontFamily: MONO, fontSize: 10.5, color: C.dim, lineHeight: 1.6 } as const;
const input = {
  background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: C.text,
  fontFamily: MONO, fontSize: 11.5, padding: "5px 8px", outline: "none",
} as const;

function Block({ text, id }: { text: string; id: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex items-start gap-2" style={{ background: C.bg, border: `1px solid ${C.line}`, borderRadius: 6, padding: "8px 10px" }}>
      <pre data-testid={id} style={{ margin: 0, flex: 1, fontFamily: MONO, fontSize: 11.5, color: C.text,
                                     whiteSpace: "pre-wrap", lineHeight: 1.6 }}>{text}</pre>
      <button aria-label="copy" onClick={() => { void navigator.clipboard?.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1200); }}
        style={btn({ color: C.mut, fontSize: 10, padding: "2px 6px" })}>
        {copied ? <Check size={11} /> : <Copy size={11} />}
      </button>
    </div>
  );
}

export default function GuardOnboarding() {
  // the shared record of the agent's tools (store.toolPolicy): what was
  // classified here shows in Policy and in a test run, and the other way round
  const policy = useApp((s) => s.toolPolicy);
  const setToolClass = useApp((s) => s.setToolClass);
  const tools: GuardTool[] = Object.entries(policy).map(([name, c]) =>
    ({ name, url: c.url ?? "", klass: c.klass, outside: c.outside }));
  const [draft, setDraft] = useState({ name: "", url: "" });
  const [savedManifests, setSavedManifests] = useState(false);
  const [nodeId, setNodeId] = useState("agent-1");
  const [key, setKey] = useState<string | null>(null);
  const connect = useApp((s) => s.connect);
  const email = useApp((s) => s.identityEmail);
  const token = useApp((s) => s.apiToken);
  const auth = useQuery({ queryKey: ["auth-status"], queryFn: api.authStatus, staleTime: 60_000 });
  const authOn = auth.data?.auth_enabled ?? null;
  const signedIn = !!email || (!!token && !isGuestToken(token));
  const needsKey = authOn !== false;
  const nodes = useQuery({ queryKey: ["nodes"], queryFn: api.nodes, refetchInterval: 3000 });
  const live = (nodes.data ?? []).some((n) => n.node_id === nodeId.trim());

  const mint = useMutation({
    mutationFn: () => api.createKey(["ingest"], `node ${nodeId}`, nodeId),
    onSuccess: (k) => setKey(k.secret),
  });
  const manifests = useMutation({
    mutationFn: () => api.wrapManifests(tools.map((t) => ({
      id: t.name, source: policy[t.name]?.source ?? t.url, description: "",
      args_schema: policy[t.name]?.args_schema ?? { type: "object" },
      // with no named driving argument every argument can drive — the safe
      // reading at an HTTP boundary; a scanned tool keeps the ones it found
      effect: { default_class: t.klass, driving_args: policy[t.name]?.driving ?? [],
                untrusted_fields: untrustedFields(policy[t.name] ?? t) },
    }))),
    onSuccess: (bundle) => { save("axor.manifests.json", bundle); setSavedManifests(true); },
  });

  const add = () => {
    const name = draft.name.trim();
    const url = draft.url.trim();
    if (!name || !url || tools.some((t) => t.name === name)) return;
    setToolClass(name, { url });
    setDraft({ name: "", url: "" });
    setSavedManifests(false);
  };
  const patch = (i: number, p: Partial<GuardTool>) => {
    const { name, ...rest } = { ...tools[i], ...p };
    setToolClass(name, rest);
    setSavedManifests(false);
  };
  const missingUrl = tools.filter((t) => !t.url).map((t) => t.name);

  const run = [
    "uv tool install axor-proxy        # or: pipx install axor-proxy",
    "",
    `export AXOR_PLANE_URL=${window.location.origin}`,
    needsKey ? `export AXOR_INGEST_KEY=${key ?? "<create the key above>"}` : null,
    `axor-proxy --guard --config tools.json --manifests axor.manifests.json --node-id ${nodeId || "agent-1"}`,
  ].filter((l) => l !== null).join("\n");
  const urls = (tools.length ? tools : EXAMPLE).map((t) => `${t.name}  →  ${LOCAL_PROXY}/t/${t.name}/`).join("\n");

  return (
    <div data-testid="guard-onboarding">
      <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 4px" }}>A proxy that guards your agent's tools</h1>
      <div style={{ fontSize: 13.5, color: C.mut, lineHeight: 1.6 }}>
        It runs beside your agent, in front of its tools. Every call is decided before it reaches the
        tool: a value from a tool that brings in outside text cannot drive one that sends something
        out, and pause / stop from this plane hold its tool calls. No change to your agent's code.
      </div>

      {step(1, "YOUR AGENT'S TOOLS — AND WHAT EACH ONE DOES")}
      <div className="flex gap-2 flex-wrap items-center" style={{ marginBottom: 8 }}>
        <ScanTools testId="guard-scan" />
        {tools.length === 0 && (
          <button onClick={() => { for (const t of EXAMPLE) setToolClass(t.name, t); setSavedManifests(false); }}
            data-testid="guard-example"
            style={btn({ color: C.steel, borderColor: C.line, fontSize: 11.5, padding: "5px 12px" })}>
            <Plug size={12} /> Load example tools
          </button>
        )}
      </div>
      <div style={{ border: `1px solid ${C.line}`, borderRadius: 8 }} data-testid="guard-tools">
        {tools.map((t, i) => (
          <div key={t.name} className="px-3 py-2 flex items-center gap-3 flex-wrap" data-testid={`guard-tool-${t.name}`}
            style={{ borderTop: i ? `1px solid ${C.line}` : "none", fontFamily: MONO, fontSize: 11.5 }}>
            <span style={{ color: C.text, minWidth: 110 }}>{t.name}</span>
            {t.url ? (
              <span style={{ color: C.dim, flex: 1, minWidth: 160, overflow: "hidden", textOverflow: "ellipsis" }}>{t.url}</span>
            ) : (
              <input placeholder="where it lives: https://… ⏎" aria-label={`where ${t.name} lives`}
                onKeyDown={(e) => { if (e.key === "Enter" && e.currentTarget.value.trim()) patch(i, { url: e.currentTarget.value.trim() }); }}
                onBlur={(e) => { if (e.currentTarget.value.trim()) patch(i, { url: e.currentTarget.value.trim() }); }}
                style={{ ...input, flex: 1, minWidth: 160, borderColor: C.amber }} />
            )}
            <select value={t.klass} onChange={(e) => patch(i, { klass: e.target.value as Klass })}
              aria-label={`what ${t.name} does`} style={{ ...input, fontSize: 11, padding: "3px 6px" }}>
              {CLASSES.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
            </select>
            <label title="its answers come from outside (web, email, files): they cannot drive a send, write or run" style={{ color: C.mut, display: "flex", alignItems: "center", gap: 5, cursor: "pointer" }}>
              <input type="checkbox" checked={t.outside} onChange={(e) => patch(i, { outside: e.target.checked })} />
              brings in outside text
            </label>
          </div>
        ))}
        <div className="px-3 py-2 flex items-center gap-2" style={{ borderTop: tools.length ? `1px solid ${C.line}` : "none" }}>
          <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })}
            onKeyDown={(e) => e.key === "Enter" && add()} placeholder="tool name ⏎" style={{ ...input, width: 130 }} />
          <input value={draft.url} onChange={(e) => setDraft({ ...draft, url: e.target.value })}
            onKeyDown={(e) => e.key === "Enter" && add()} placeholder="https://where-it-lives ⏎" style={{ ...input, flex: 1 }} />
        </div>
      </div>
      <div style={{ ...hint, marginTop: 6 }}>
        an MCP server is one tool here (its URL); its own tools are named by what the agent calls.
        {missingUrl.length > 0 && (
          <span style={{ color: C.amber }}> · the scanner found the tools, not where they live: add the address
            of {missingUrl.join(", ")}.</span>
        )}
      </div>

      {step(2, "SAVE TWO FILES — NEXT TO THE PROXY")}
      <div className="flex gap-2 flex-wrap">
        <button onClick={() => save("tools.json", { tools: Object.fromEntries(tools.filter((t) => t.url).map((t) => [t.name, t.url])) })}
          disabled={tools.length === 0} data-testid="guard-save-tools"
          style={btn({ color: C.text, borderColor: C.steel, fontSize: 11.5, padding: "5px 12px" })}>
          <Download size={12} /> tools.json
        </button>
        <button onClick={() => manifests.mutate()} disabled={tools.length === 0 || manifests.isPending}
          data-testid="guard-save-manifests"
          style={btn({ color: C.text, borderColor: C.steel, fontSize: 11.5, padding: "5px 12px" })}>
          {savedManifests ? <Check size={12} /> : <Download size={12} />} axor.manifests.json
        </button>
      </div>
      <div style={{ ...hint, marginTop: 6 }}>
        tools.json says where each tool lives; axor.manifests.json what each one does — what the proxy decides with.
      </div>
      {manifests.isError && <div style={{ ...hint, color: C.red }}>{(manifests.error as Error).message}</div>}

      {step(3, "CONNECT IT TO THIS WORKSPACE")}
      {needsKey && authOn && !signedIn ? (
        <div style={{ border: `1px solid ${C.line}`, borderRadius: 8, padding: 14 }}>
          <div style={{ ...hint, color: C.mut, fontSize: 11, marginBottom: 10 }}>
            The proxy reports into your workspace, so this step needs an account — free, a minute.
          </div>
          <IdentityLogin initialMode="signup" />
        </div>
      ) : (
        <div className="flex items-center gap-2 flex-wrap">
          <span style={{ ...hint, color: C.mut, fontSize: 11 }}>agent name</span>
          <input value={nodeId} onChange={(e) => { setNodeId(e.target.value); setKey(null); }}
            aria-label="agent name" style={{ ...input, width: 160 }} />
          {needsKey && !key && (
            <button onClick={() => mint.mutate()} disabled={!nodeId.trim() || mint.isPending} data-testid="guard-mint"
              style={btn({ color: C.steel, borderColor: C.steel, fontSize: 11, padding: "4px 10px" })}>
              {mint.isPending ? "…" : "create the key"}
            </button>
          )}
          {key && <span style={{ ...hint, color: C.amber }}>the key is shown once — copy it now</span>}
          {!needsKey && <span style={hint}>this plane is open — no key needed</span>}
          {mint.isError && <div style={{ ...hint, color: C.red, width: "100%" }}>{(mint.error as Error).message}</div>}
        </div>
      )}

      {step(4, "RUN THE PROXY BESIDE YOUR AGENT")}
      <Block id="guard-run" text={run} />

      {step(5, "POINT YOUR AGENT AT IT")}
      <Block id="guard-urls" text={urls} />
      <div style={{ ...hint, marginTop: 6 }}>
        send <span style={{ color: C.mut }}>X-Axor-Session: &lt;conversation id&gt;</span> with each call to keep one
        conversation's outside text out of another's. A refused call answers 403 (MCP: a JSON-RPC error);
        while paused, 503.
      </div>

      {step(6, "RUN IT")}
      <div data-testid="guard-live" className="p-3" style={{ background: C.panel, borderRadius: 8, fontFamily: MONO, fontSize: 11.5,
        border: `1px solid ${live ? C.green : C.line}` }}>
        {live
          ? <span style={{ color: C.green }}>● {nodeId} reported in — the proxy is guarding it</span>
          : <span style={{ color: C.mut }}>waiting for <span style={{ color: C.text }}>{nodeId || "agent-1"}</span> to report in — start the proxy…</span>}
      </div>
      {live && (
        <button onClick={() => {
          // the agent's tools stay on the connection: a chaos test on this
          // agent offers them, not the demo's web_search / mcp
          connect("adapter", tools.map((t) => ({ name: t.name, url: t.url })));
          navigate(`govern/${encodeURIComponent(nodeId)}`);
        }}
          className="mt-4" style={btn({ color: C.text, borderColor: C.green, fontSize: 12, padding: "7px 16px" })}>
          See it in Agents →
        </button>
      )}
    </div>
  );
}
