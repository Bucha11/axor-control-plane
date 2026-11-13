// Get started: tools → point agent → connection check (onboarding mockup).
// Step 3 is wired to the real proxy preflight endpoint via react-query.
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { ArrowRight, Check, Circle, Copy, Plug, RefreshCw, Zap } from "lucide-react";
import { api } from "../api";
import { navigate, useRoute } from "../router";
import CodingOnboarding from "../components/CodingOnboarding";
import type { Journey } from "./Pricing";
import { ConnectionMode, useApp } from "../store";
import { C, MONO, btn } from "../theme";
import Coach from "../components/Coach";
import Tooltip from "../components/Tooltip";

interface Tool {
  name: string;
  url: string;
}

const TOOLS: Tool[] = [
  { name: "web_search", url: "https://api.search.example/v1" },
  { name: "send_report", url: "https://slack.example/api/post" },
  { name: "run_query", url: "https://db.internal.example/query" },
];

// What `axor-proxy --demo` registers: preflight answers for these out of the box.
const MOCK_TOOLS: Tool[] = [
  { name: "web_search", url: `${(import.meta.env.VITE_PROXY_PUBLIC_URL ?? "http://127.0.0.1:8401").replace(/\/$/, "")}/mock/web_search` },
  { name: "mcp", url: `${(import.meta.env.VITE_PROXY_PUBLIC_URL ?? "http://127.0.0.1:8401").replace(/\/$/, "")}/mock/mcp` },
];

type RowState = "idle" | "testing" | "ok" | "fail" | "unregistered";

// Where an agent reaches the proxy's tool routes (/t/{tool}/). The UI's own
// origin only fronts /axor and /v1, so this is the proxy's address, not ours.
const PROXY_PUBLIC_URL = (import.meta.env.VITE_PROXY_PUBLIC_URL ?? "http://127.0.0.1:8401")
  .replace(/\/$/, "");

// Parse either a full client config ({"mcpServers": {name: {url | command}}})
// — the shape Claude Desktop / Cursor use — or a bare server URL, or a bare
// command line ("npx -y @modelcontextprotocol/server-…"). stdio servers go
// through the proxy's local gateway; HTTP servers are dialed directly.
type McpTarget = { name?: string; url?: string; command?: string[] };

function parseMcpConfig(raw: string): McpTarget[] {
  const trimmed = raw.trim();
  if (/^https?:\/\//.test(trimmed)) return [{ url: trimmed }];
  let parsed: Record<string, unknown>;
  try {
    parsed = JSON.parse(trimmed) as Record<string, unknown>;
  } catch {
    // Not JSON and not a URL: treat it as a stdio command line.
    const command = trimmed.split(/\s+/).filter(Boolean);
    return command.length ? [{ command }] : [];
  }
  const entries = (parsed.mcpServers ?? parsed) as Record<string, Record<string, unknown>>;
  const targets: McpTarget[] = [];
  for (const [name, spec] of Object.entries(entries)) {
    if (typeof spec !== "object" || spec === null) continue;
    const url = (spec.url ?? spec.serverUrl) as string | undefined;
    if (typeof url === "string" && url) targets.push({ name, url });
    else if (typeof spec.command === "string" && spec.command) {
      const args = Array.isArray(spec.args)
        ? spec.args.filter((a): a is string => typeof a === "string")
        : [];
      targets.push({ name, command: [spec.command, ...args] });
    }
  }
  return targets;
}

// Three ways in. A team that wants its coding agents governed has no tools to
// declare and no base URLs to repoint; a team wrapping a production agent does
// not care about Claude Code. Each gets its own path; "both" walks both.
export const WAYS: { id: Journey; title: string; body: string }[] = [
  { id: "coding", title: "Coding agents",
    body: "Claude Code and Codex, through their hooks. Two commands; every session shows up here." },
  { id: "production", title: "My production agent",
    body: "Your own agent in production: put the proxy in front of its tools, or wrap it with an adapter." },
  { id: "both", title: "Both",
    body: "Your developers' coding agents and your production fleet, on one plane." },
];

export default function Onboarding() {
  const route = useRoute();
  // the old /get-started link was the proxy walkthrough: it still is
  const initial = route.query.for ?? (route.segments[0] === "get-started" ? "production" : undefined);
  const [way, setWay] = useState<Journey | null>(
    initial === "coding" || initial === "production" || initial === "both" ? initial : null);
  const [codingDone, setCodingDone] = useState(false);
  const choose = (j: Journey) => { setWay(j); setCodingDone(false); navigate(`connect?for=${j}`); };

  if (way === null) {
    return (
      <div style={{ maxWidth: 640, margin: "0 auto" }} data-testid="journey-chooser">
        <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 4px" }}>What do you want to govern?</h1>
        <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, marginBottom: 16 }}>
          Pick one — you can add the other later.
        </div>
        <div className="flex flex-col gap-3">
          {WAYS.map((w) => (
            <button key={w.id} data-testid={`way-${w.id}`} onClick={() => choose(w.id)}
              style={{
                textAlign: "left", cursor: "pointer", background: C.panel, color: C.text,
                border: `1px solid ${C.line}`, borderRadius: 8, padding: "14px 16px",
              }}>
              <div style={{ fontFamily: MONO, fontSize: 13.5, fontWeight: 700, marginBottom: 4 }}>{w.title}</div>
              <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, lineHeight: 1.5 }}>{w.body}</div>
            </button>
          ))}
        </div>
      </div>
    );
  }

  const back = (
    <button onClick={() => { setWay(null); navigate("connect"); }}
      style={btn({ color: C.dim, fontSize: 10.5, padding: "3px 10px", marginBottom: 14 })}>
      ← choose another way in
    </button>
  );
  if (way === "coding" || (way === "both" && !codingDone)) {
    return (
      <div style={{ maxWidth: 640, margin: "0 auto" }}>
        {back}
        <CodingOnboarding then={way === "both" ? () => setCodingDone(true) : undefined} />
      </div>
    );
  }
  return (
    <div>
      <div style={{ maxWidth: 640, margin: "0 auto" }}>{back}</div>
      <ProductionOnboarding />
    </div>
  );
}

function ProductionOnboarding() {
  const [step, setStep] = useState(1);
  const [tools, setTools] = useState<Tool[]>([]);
  const [copied, setCopied] = useState<string | null>(null);
  const [draftName, setDraftName] = useState("");
  const [draftUrl, setDraftUrl] = useState("");
  const [mcpRaw, setMcpRaw] = useState("");
  const [mcpBusy, setMcpBusy] = useState(false);
  const [mcpNote, setMcpNote] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [mcpTools, setMcpTools] = useState<Record<string, string[]>>({});

  const discoverMcp = async (): Promise<void> => {
    setMcpBusy(true);
    setMcpNote(null);
    try {
      const targets = parseMcpConfig(mcpRaw);
      if (targets.length === 0) {
        setMcpNote({
          kind: "err",
          text: "no MCP servers found — paste {\"mcpServers\": {…}}, an http(s) url, or a stdio command line",
        });
        return;
      }
      const added: Tool[] = [];
      const found: Record<string, string[]> = {};
      for (const t of targets) {
        const info = await api.mcpDiscover(
          t.url ? { url: t.url } : { command: t.command },
          t.name,
        );
        added.push({
          name: info.registered,
          url: t.url ?? `stdio: ${t.command?.join(" ") ?? ""}`,
        });
        found[info.registered] = info.tools.map((tl) => tl.name);
      }
      setTools((prev) => [
        ...prev.filter((t) => !added.some((a) => a.name === t.name)),
        ...added,
      ]);
      setMcpTools((prev) => ({ ...prev, ...found }));
      const total = Object.values(found).reduce((n, ts) => n + ts.length, 0);
      setMcpNote({
        kind: "ok",
        text: `registered ${added.length} MCP server${added.length === 1 ? "" : "s"} · ${total} tools discovered`,
      });
      setMcpRaw("");
    } catch (e) {
      setMcpNote({ kind: "err", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setMcpBusy(false);
    }
  };
  // Adapter path is chosen up front (spec section 2, Axis A): it unlocks the
  // Control plane, taint graph and probe health. Proxy is the default depth.
  const [depth, setDepth] = useState<Extract<ConnectionMode, "proxy" | "adapter">>("proxy");
  // Adapter path: the node needs a scoped ingest credential to speak on the
  // plane at all once the backend has auth on, and it should be bound to this
  // node so it cannot speak for another. Minting it is an admin action, so it
  // happens here rather than being left as an undocumented prerequisite.
  const [nodeId, setNodeId] = useState("agent-1");
  const [framework, setFramework] = useState<Framework>("langchain");
  const [nodeKey, setNodeKey] = useState<string | null>(null);
  const mintNodeKey = useMutation({
    mutationFn: () => api.createKey(["ingest"], `node ${nodeId}`, nodeId),
    onSuccess: (k) => setNodeKey(k.secret),
  });
  const connect = useApp((s) => s.connect);

  const preflight = useMutation({ mutationFn: api.proxyPreflight });

  const finish = (): void => {
    connect(depth, tools);
    navigate("catch");
  };

  const addTool = () => {
    const name = draftName.trim();
    const url = draftUrl.trim();
    if (!name || !url) return;
    if (tools.some((t) => t.name === name)) return;
    setTools([...tools, { name, url }]);
    setDraftName("");
    setDraftUrl("");
  };

  const rowState = (name: string): RowState => {
    if (preflight.isPending) return "testing";
    if (!preflight.data) return "idle";
    // Preflight only pings tools the proxy was started with (tools.json or MCP
    // discovery); a tool declared only here has no route through it.
    const entry = preflight.data.tools[name];
    if (!entry) return "unregistered";
    return entry.ok ? "ok" : "fail";
  };
  const result = preflight.data;
  // Tools the proxy does not know are flagged, not blocking: the experiment
  // runs against the proxy's own tools either way.
  const known = result ? tools.filter((t) => result.tools[t.name]) : [];
  const allGreen = known.length > 0 && known.every((t) => result!.tools[t.name].ok);

  const StepDot = ({ n, label }: { n: number; label: string }) => (
    <div className="flex items-center gap-2">
      <span style={{
        width: 20, height: 20, borderRadius: 10, display: "flex", alignItems: "center", justifyContent: "center",
        fontFamily: MONO, fontSize: 10, fontWeight: 700,
        background: step > n ? C.green : step === n ? "rgba(127,168,204,0.15)" : C.panel2,
        color: step > n ? C.bg : step === n ? C.steel : C.dim,
        border: `1px solid ${step === n ? C.steel : C.line}`,
      }}>{step > n ? <Check size={11} /> : n}</span>
      <span style={{ fontFamily: MONO, fontSize: 11, color: step === n ? C.text : C.dim }}>{label}</span>
    </div>
  );

  const addRow = (
    <div className="flex items-center gap-2">
      <input value={draftName} onChange={(e) => setDraftName(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && addTool()} placeholder="tool name ⏎"
        style={{ width: 140, background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: C.text, fontFamily: MONO, fontSize: 11.5, padding: "6px 8px", outline: "none" }} />
      <input value={draftUrl} onChange={(e) => setDraftUrl(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && addTool()} placeholder="https://endpoint ⏎"
        style={{ flex: 1, background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: C.text, fontFamily: MONO, fontSize: 11.5, padding: "6px 8px", outline: "none" }} />
    </div>
  );

  return (
    <div style={{ maxWidth: 640, margin: "0 auto" }}>
      <Coach id="onboarding" title="Get started — put the proxy in front of your agent">
        Three steps: declare the tools your agent calls, repoint its base URLs at
        the proxy (auth passes through byte-for-byte, nothing else changes), and
        test each connection. Green lights mean you can run the same Eval loop on
        your own agent — pick <span style={{ color: C.text }}>proxy</span> for
        observe-only or <span style={{ color: C.text }}>adapter</span> for full
        governance + Control.
      </Coach>
      <div className="flex gap-6 mb-8">
        <StepDot n={1} label="tools" /><StepDot n={2} label="point agent" /><StepDot n={3} label="connection check" />
      </div>

      {step === 1 && (
        <>
          <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 4px" }}>What tools does your agent use?</h1>
          <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, marginBottom: 14 }}>The proxy will sit in front of these. Auth passes through untouched.</div>

          {/* MCP-first path: paste the client config you already have. */}
          <div className="p-3 mb-4" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
            <div style={{ fontSize: 10, fontFamily: MONO, color: C.dim, letterSpacing: "0.1em", marginBottom: 8 }}>
              MCP · PASTE YOUR CLIENT CONFIG
            </div>
            <textarea
              value={mcpRaw}
              onChange={(e) => setMcpRaw(e.target.value)}
              rows={3}
              spellCheck={false}
              placeholder={'{"mcpServers": {"docs": {"url": "https://mcp.example/sse"}, "fs": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem"]}}}  ·  or a bare url / command line'}
              className="w-full mb-2"
              style={{ background: C.bg, border: `1px solid ${C.line}`, borderRadius: 5, color: C.text, fontFamily: MONO, fontSize: 11, padding: 8, resize: "vertical", outline: "none" }}
            />
            <div className="flex items-center gap-3">
              <Tooltip content="We handshake with each MCP server (initialize → tools/list), list its tools, and register it as a proxied endpoint — your agent then points at /t/{server}/ instead. HTTP servers are dialed directly; stdio servers (command:) are spawned locally by the proxy's gateway.">
                <button
                  onClick={() => void discoverMcp()}
                  disabled={mcpBusy || !mcpRaw.trim()}
                  style={btn({ color: mcpRaw.trim() ? C.steel : C.dim, borderColor: mcpRaw.trim() ? C.steel : C.line, fontSize: 12 })}
                >
                  <Plug size={13} /> {mcpBusy ? "discovering…" : "Discover MCP tools"}
                </button>
              </Tooltip>
              {mcpNote && (
                <span style={{ fontFamily: MONO, fontSize: 11, color: mcpNote.kind === "ok" ? C.green : C.red }}>
                  {mcpNote.text}
                </span>
              )}
            </div>
          </div>
          {tools.length === 0 ? (
            <div className="p-8 flex flex-col items-center gap-3" style={{ background: C.panel, border: `1px dashed ${C.line}`, borderRadius: 8 }}>
              <Tooltip content="Fills in a realistic 3-tool sample (search / report / query) so you can walk the flow without typing endpoints.">
                <button onClick={() => setTools(TOOLS)} style={btn({ color: C.steel, borderColor: C.steel, fontSize: 12 })}>
                  <Plug size={13} /> Load example tools
                </button>
              </Tooltip>
              <span style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>
                loads a sample set · or paste your MCP config above (http + stdio both work) · or add endpoints by hand · or{" "}
                <span
                  role="button"
                  onClick={() => setTools(MOCK_TOOLS)}
                  style={{ color: C.steel, cursor: "pointer" }}
                >
                  use our mock tools (zero creds)
                </span>
              </span>
              <div className="w-full">{addRow}</div>
            </div>
          ) : (
            <>
              <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
                {tools.map((t, i) => (
                  <div key={t.name} className="px-4 py-3" style={{ borderTop: i ? `1px solid ${C.line}` : "none" }}>
                    <div className="flex items-center gap-3">
                      <span style={{ fontFamily: MONO, fontSize: 13, color: C.text, width: 130 }}>{t.name}</span>
                      <span style={{ fontFamily: MONO, fontSize: 11, color: C.dim, flex: 1 }}>{t.url}</span>
                      {mcpTools[t.name] && (
                        <span style={{ fontFamily: MONO, fontSize: 10, color: C.steel }}>MCP</span>
                      )}
                    </div>
                    {mcpTools[t.name] && (
                      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, marginTop: 4, paddingLeft: 0 }}>
                        tools: {mcpTools[t.name].join(" · ")}
                      </div>
                    )}
                  </div>
                ))}
                <div className="px-4 py-3" style={{ borderTop: `1px solid ${C.line}` }}>{addRow}</div>
              </div>
              <button onClick={() => setStep(2)} className="mt-4" style={btn({ color: C.text, borderColor: C.steel, fontSize: 12.5 })}>
                Continue <ArrowRight size={13} />
              </button>
            </>
          )}
        </>
      )}

      {step === 2 && (
        <>
          <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 4px" }}>Point your agent at the proxy.</h1>
          <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, marginBottom: 20 }}>Replace each base URL in your agent config. Nothing else changes.</div>
          <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
            {tools.map((t, i) => {
              const proxied = `${PROXY_PUBLIC_URL}/t/${t.name}/`;
              return (
                <div key={t.name} className="flex items-center gap-3 px-4 py-3" style={{ borderTop: i ? `1px solid ${C.line}` : "none" }}>
                  <span style={{ fontFamily: MONO, fontSize: 12, color: C.mut, width: 130 }}>{t.name}</span>
                  <span style={{ fontFamily: MONO, fontSize: 12, color: C.text, flex: 1 }}>{proxied}</span>
                  <button onClick={() => {
                    void navigator.clipboard?.writeText(proxied);
                    setCopied(t.name);
                    setTimeout(() => setCopied(null), 1200);
                  }}
                    style={btn({ padding: "4px 8px", color: copied === t.name ? C.green : C.mut })}>
                    {copied === t.name ? <Check size={12} /> : <Copy size={12} />}
                  </button>
                </div>
              );
            })}
          </div>
          <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 10 }}>
            endpoint is armed only while a session is active — disarmed it returns 503
          </div>
          <button onClick={() => setStep(3)} className="mt-4" style={btn({ color: C.text, borderColor: C.steel, fontSize: 12.5 })}>
            Continue <ArrowRight size={13} />
          </button>
        </>
      )}

      {step === 3 && (
        <>
          <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 4px" }}>Prove the plumbing before it matters.</h1>
          <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, marginBottom: 20 }}>
            A red light here is routine. A red light mid-experiment ruins the run — so we check now.
          </div>
          <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
            {tools.map((t, i) => {
              const st = rowState(t.name);
              const entry = preflight.data?.tools[t.name];
              const failLabel = entry && !entry.ok
                ? (entry.error ?? (entry.status != null ? `${entry.status} from upstream` : "unreachable"))
                : "";
              return (
                <div key={t.name} className="px-4 py-3" style={{ borderTop: i ? `1px solid ${C.line}` : "none" }}>
                  <div className="flex items-center gap-3">
                    {st === "testing"
                      ? <RefreshCw size={12} color={C.steel} className="animate-spin" />
                      : <Circle size={9} fill={st === "ok" ? C.green : st === "fail" ? C.red : C.dim} color={st === "ok" ? C.green : st === "fail" ? C.red : C.dim} />}
                    <span style={{ fontFamily: MONO, fontSize: 13, color: C.text, flex: 1 }}>{t.name}</span>
                    <span style={{ fontFamily: MONO, fontSize: 11, color: st === "ok" ? C.green : st === "fail" ? C.red : st === "unregistered" ? C.amber : C.dim }}>
                      {st === "ok" ? "reachable · auth passes" : st === "fail" ? failLabel : st === "testing" ? "pinging…" : st === "unregistered" ? "not registered on the proxy" : "untested"}
                    </span>
                  </div>
                  {st === "unregistered" && (
                    <div className="flex items-center gap-3 mt-2" style={{ paddingLeft: 21 }}>
                      <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>
                        The proxy has no route for {t.name}. Add it under "tools" in the proxy's tools.json ({`"${t.name}": "${t.url}"`}), restart with --config tools.json, then re-test.
                      </span>
                    </div>
                  )}
                  {st === "fail" && (
                    <div className="flex items-center gap-3 mt-2" style={{ paddingLeft: 21 }}>
                      <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>
                        Your agent's credential for {t.name} was rejected upstream. The proxy forwards auth byte-for-byte — fix the credential in your agent config, then re-test.
                      </span>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
          {result && known.length === 0 && (
            <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.amber, marginTop: 10 }}>
              none of these tools is registered on the proxy — start it with --config tools.json (or --demo for the mock tools)
            </div>
          )}
          {preflight.isError && (
            <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.red, marginTop: 10 }}>
              proxy unreachable at /axor — start it: uvx axor-proxy --demo
            </div>
          )}
          <div className="flex items-center gap-3 mt-4">
            <Tooltip content="Pings every declared tool through the proxy right now — a red light here is routine; a red light mid-experiment ruins the run.">
              <button onClick={() => preflight.mutate()} style={btn({ color: C.text, borderColor: C.steel, fontSize: 12.5 })}>
                <RefreshCw size={13} /> {preflight.data || preflight.isError ? "Re-test all" : "Test connections"}
              </button>
            </Tooltip>
            {allGreen && (
              <button onClick={finish} style={btn({ color: C.bg, background: C.green, borderColor: C.green, fontSize: 12.5, fontWeight: 700 })}>
                <Zap size={13} /> Run first experiment <ArrowRight size={13} />
              </button>
            )}
          </div>
          <div className="flex items-center gap-3 mt-4" style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>
            connect as
            {(["proxy", "adapter"] as const).map((d) => (
              <Tooltip
                key={d}
                content={d === "proxy"
                  ? "Observe-only: the proxy watches tool traffic and catches discrepancies. No code change in your agent."
                  : "Full governance: wrap your agent in an axor-core Invokable — per-value taint, budgets, and the live Control plane."}
              >
                <button onClick={() => setDepth(d)}
                  style={btn({
                    color: depth === d ? C.steel : C.dim,
                    borderColor: depth === d ? C.steel : C.line,
                    fontSize: 11, padding: "4px 10px",
                  })}>
                  {d}
                </button>
              </Tooltip>
            ))}
            <span style={{ color: C.dim }}>
              {depth === "adapter" ? "unlocks Control, taint graph, probe health" : "Eval core — Control is greyed until you wrap"}
            </span>
          </div>
          {depth === "adapter" && (
            <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8, marginTop: 12, overflow: "hidden" }}>
              <div style={{ padding: "10px 14px", borderBottom: `1px solid ${C.line}`, fontFamily: MONO, fontSize: 11, color: C.mut }}>
                the adapter is code, not a toggle — this button and snippet are the whole setup
              </div>
              <div className="px-4 py-3" style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                <span style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut }}>node id</span>
                <input value={nodeId} onChange={(e) => { setNodeId(e.target.value); setNodeKey(null); }}
                  style={{ background: C.panel2, border: `1px solid ${C.line}`, borderRadius: 5, color: C.text,
                           fontFamily: MONO, fontSize: 12, padding: "5px 9px", width: 160 }} />
                <Tooltip content="Mints an ingest-scoped key bound to this node. The plane refuses it for any other node, so a compromised node cannot forge a neighbour's heartbeat or health verdict.">
                  <button onClick={() => mintNodeKey.mutate()} disabled={!nodeId.trim() || mintNodeKey.isPending}
                    style={btn({ color: C.text, borderColor: C.steel, fontSize: 12 })}>
                    <Plug size={12} /> {mintNodeKey.isPending ? "minting…" : "Mint node-bound key"}
                  </button>
                </Tooltip>
                {mintNodeKey.isError && (
                  <span style={{ fontFamily: MONO, fontSize: 11, color: C.red }}>
                    {(mintNodeKey.error as Error).message} — minting needs an admin credential (operator token in Settings)
                  </span>
                )}
              </div>
              {nodeKey && (
                <div className="px-4 pb-3" style={{ fontFamily: MONO, fontSize: 11, color: C.amber }}>
                  shown once — copy it now, only its hash is stored
                </div>
              )}
              <div className="px-4 pb-3" style={{ display: "flex", gap: 6, flexWrap: "wrap" }} data-testid="framework-picker">
                {FRAMEWORKS.map((f) => (
                  <button key={f.id} onClick={() => setFramework(f.id)}
                    style={btn({
                      color: framework === f.id ? C.steel : C.dim,
                      borderColor: framework === f.id ? C.steel : C.line,
                      fontSize: 11, padding: "4px 10px",
                    })}>
                    {f.label}
                  </button>
                ))}
              </div>
              <pre data-testid="adapter-snippet" style={{ margin: 0, padding: "12px 14px", background: C.panel2, borderTop: `1px solid ${C.line}`,
                            fontFamily: MONO, fontSize: 11.5, color: C.text, overflowX: "auto", lineHeight: 1.7 }}>
{adapterSnippet(framework, {
  origin: window.location.origin,
  nodeId: nodeId || "agent-1",
  key: nodeKey ?? "<mint a key above>",
  tools: tools.map((t) => t.name),
})}
              </pre>
              <div className="px-4 py-3" style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, lineHeight: 1.6, borderTop: `1px solid ${C.line}` }}>
                {FRAMEWORKS.find((f) => f.id === framework)?.note}
              </div>
            </div>
          )}
          {depth === "proxy" && (
            <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 8, lineHeight: 1.6, border: `1px solid ${C.line}`, borderRadius: 6, padding: "8px 10px" }}>
              visibility: a hosted proxy sees your tool traffic (URLs, params, results) to observe it — that is how the audit works.
              your credentials pass through byte-for-byte and are never stored. self-host the proxy to keep every byte on your own infrastructure;
              stored artifacts are observations and labels only, never raw request/response bodies (spec §8.3).
            </div>
          )}
          {allGreen && (
            <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim, marginTop: 12 }}>
              optional: run a baseline health check first (23 probes, ~1 min) — gives drift comparison later
            </div>
          )}
        </>
      )}
    </div>
  );
}

type Framework = "langchain" | "claude" | "python";

const FRAMEWORKS: { id: Framework; label: string; note: string }[] = [
  {
    id: "langchain", label: "LangChain",
    note: "the middleware connects on its own (background thread, sync or async agent): pause / stop hold every model and "
      + "tool call, budget caps tool calls, an excision cuts a tool result from the next model call (same thread_id), "
      + "and every tool call and result lands in Replay with its taint. pip install 'axor-langchain[plane,security]' "
      + "adds cross-run reputation (Sentinel); AxorMiddleware(probe=True) adds a health check after each run — it costs "
      + "model calls — whose localizer proposes the cut in Health. [experiment] adds Test honesty: break a tool on the "
      + "next run and catch a fabricated answer (AXOR_TEST_BENCH=1 on the agent). AXOR_PLANE_URL unset = no plane, nothing changes.",
  },
  {
    id: "claude", label: "Claude (axor-claude)",
    note: "make_session() connects on its own (background link): pause / stop hold the loop at the intent boundary, "
      + "budget caps tool calls, an excision cuts a tool result (tool_use_id) from the conversation and the file it "
      + "read from core's context, and every run lands in Replay. [security] adds Sentinel reputation and "
      + "make_session(probe=True) a health check (costs model calls); [experiment] adds Test honesty "
      + "(AXOR_TEST_BENCH=1). Excision and injection need axor-core ≥ 0.12.1.",
  },
  {
    id: "python", label: "Plain Python",
    note: "this is the posture half: pause / stop / budget reach the node and hold its tool calls. one-shot injection and "
      + "context excision need the agent loop in axor-core — use the Claude adapter or GovernedSession with "
      + "admission=PlaneAdmission(session).",
  },
];

export function adapterSnippet(
  framework: Framework,
  o: { origin: string; nodeId: string; key: string; tools: string[] },
): string {
  if (framework === "langchain") {
    return `pip install 'axor-langchain[plane]'

export AXOR_PLANE_URL=${o.origin}
export AXOR_NODE_ID=${o.nodeId}
export AXOR_INGEST_KEY=${o.key}

from langchain.agents import create_agent
from axor_langchain import AxorMiddleware

agent = create_agent(model, tools, middleware=[AxorMiddleware()])
agent.invoke({"messages": [("user", "...")]})   # node is live on this plane`;
  }
  if (framework === "claude") {
    return `pip install 'axor-claude[plane]'

export AXOR_PLANE_URL=${o.origin}
export AXOR_NODE_ID=${o.nodeId}
export AXOR_INGEST_KEY=${o.key}

import axor_claude

session = axor_claude.make_session()        # node is live on this plane
await session.run("...")`;
  }
  return `pip install 'axor-wrap[plane]'

from axor_wrap.runtime import wrap_callables
from axor_wrap.connect import PlaneConnector

toolset = wrap_callables({${o.tools.join(", ") || "web_search"}})

node = PlaneConnector(
    "${o.origin}", "${o.nodeId}",
    ingest_key="${o.key}",
).connect()
node.gate(toolset)          # pause/stop from Control now holds real tool calls
await node.run()            # heartbeat + desired-state subscription`;
}
