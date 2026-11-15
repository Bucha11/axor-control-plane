// Get started: tools → point agent → connection check (onboarding mockup).
// Step 3 is wired to the real proxy preflight endpoint via react-query.
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowRight, Check, Circle, Copy, Plug, RefreshCw, Zap } from "lucide-react";
import { api } from "../api";
import { navigate, useRoute } from "../router";
import CodingOnboarding, { isCodingSession } from "../components/CodingOnboarding";
import AdapterOnboarding from "../components/AdapterOnboarding";
import GuardOnboarding from "../components/GuardOnboarding";
import ScanTools from "../components/ScanTools";
import type { Journey } from "./Pricing";
import { ConnectionMode, useApp, type ToolKlass } from "../store";
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
// what the example tools do — the same reading the scanner would give them
const TOOL_CLASSES: Record<string, { klass: ToolKlass; outside: boolean }> = {
  web_search: { klass: "READ", outside: true },
  send_report: { klass: "EXPORT", outside: false },
  run_query: { klass: "READ", outside: false },
};
const KLASS_LABEL: { id: ToolKlass; label: string }[] = [
  { id: "READ", label: "reads" }, { id: "WRITE", label: "changes something" },
  { id: "EXPORT", label: "sends out" }, { id: "EXEC", label: "runs code" },
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
    body: "Your own agent in production: wrap it in code, or put a proxy in front of its tools — either one refuses a dangerous call." },
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
  const steps = way === "both" && (
    <BothSteps at={codingDone ? 2 : 1} onStep={(n) => setCodingDone(n === 2)} />
  );
  if (way === "coding" || (way === "both" && !codingDone)) {
    return (
      <div style={{ maxWidth: 640, margin: "0 auto" }}>
        {back}
        {steps}
        <CodingOnboarding then={way === "both" ? () => setCodingDone(true) : undefined} />
      </div>
    );
  }
  return (
    <div>
      <div style={{ maxWidth: 640, margin: "0 auto" }}>
        {back}
        {steps}
        {way === "both" && (
          <div data-testid="both-production-intro" style={{ fontSize: 13.5, color: C.mut, lineHeight: 1.6,
            marginBottom: 14 }}>
            <span style={{ color: C.text }}>Now your production agent.</span> First, how Axor sits with
            it: wrapped in code (inside the agent — it also sees the model) or a proxy in front of its
            tools (no code change). Both refuse a dangerous call.
          </div>
        )}
      </div>
      <ProductionOnboarding
        initial={route.segments[0] === "get-started" || route.query.how === "test" || route.query.how === "guard"
          ? "proxy" : route.query.how === "code" ? "code" : undefined}
        proxyMode={route.query.how === "guard" ? "guard" : undefined} />
    </div>
  );
}

type ProdWay = "code" | "proxy";

/** Two ways to put a production agent under Axor, and they are not the same
 * product: both refuse a dangerous call, pause, stop and cap; the wrap is inside
 * the agent and also sees the model (it can cut an item from its context), the
 * proxy needs no code change but holds only tool calls over HTTP / MCP — and its
 * test runs watch instead of blocking.
 * Said before either is set up, so nobody wires a proxy expecting a guard. */
function ProductionOnboarding({ initial, proxyMode }: { initial?: ProdWay; proxyMode?: ProxyMode }) {
  const [way, setWay] = useState<ProdWay | null>(initial ?? null);
  if (way === "code") {
    return (
      <div style={{ maxWidth: 640, margin: "0 auto" }}>
        <WaySwitch way={way} setWay={setWay} />
        <AdapterOnboarding />
      </div>
    );
  }
  if (way === "proxy") {
    return (
      <div style={{ maxWidth: 640, margin: "0 auto" }}>
        <WaySwitch way={way} setWay={setWay} />
        <ProxyModes onWrap={() => setWay("code")} initial={proxyMode ?? (initial === "proxy" ? "test" : "guard")} />
      </div>
    );
  }
  const yes = <span style={{ color: C.green }}>yes</span>;
  const no = <span style={{ color: C.dim }}>no</span>;
  const rows: [string, React.ReactNode, React.ReactNode][] = [
    ["sits", "inside the agent", "in front of its tools, beside the agent"],
    ["change to your agent", "a few lines of code", "none — swap its tool URLs"],
    ["refuses a dangerous tool call", yes, yes],
    ["pause, stop, call budget — live", <>{yes} — the whole agent</>, <>{yes} — its tool calls; the agent still talks</>],
    ["sees the model: cut a bad item from its context", <>{yes} (LangChain, Claude)</>, no],
    ["tools inside the agent (functions, files, shell)", yes, <>{no} — only calls over HTTP or MCP</>],
    ["catches a made-up answer", <>{yes} (opt-in)</>, <>{yes} — in test runs, it breaks a tool on purpose</>],
    ["works with", "Python: LangChain, Claude Agent, tool functions", "any language or platform"],
  ];
  return (
    <div style={{ maxWidth: 640, margin: "0 auto" }} data-testid="prod-way-chooser">
      <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 4px" }}>How should Axor sit with your agent?</h1>
      <div style={{ fontSize: 13.5, color: C.mut, lineHeight: 1.6, marginBottom: 14 }}>
        Both can refuse a dangerous tool call. The wrap sits inside the agent and also reaches the
        model; the proxy sits in front of its tools — no code change, any language.
      </div>
      <div className="flex gap-3 flex-wrap" style={{ marginBottom: 14 }}>
        {([
          ["code", "Wrap it in code", "the deepest control: every tool call checked, and the agent itself paused, stopped and repaired live"],
          ["proxy", "Put a proxy in front of its tools", "no code change, any language: tool calls checked and held — or, in a test run, only watched"],
        ] as [ProdWay, string, string][]).map(([id, title, body]) => (
          <button key={id} onClick={() => setWay(id)} data-testid={`prod-way-${id}`}
            style={{ flex: "1 1 260px", textAlign: "left", cursor: "pointer", background: C.panel, color: C.text,
                     border: `1px solid ${id === "code" ? C.steel : C.line}`, borderRadius: 8, padding: "14px 16px" }}>
            <div style={{ fontFamily: MONO, fontSize: 13.5, fontWeight: 700, marginBottom: 4 }}>{title}</div>
            <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, lineHeight: 1.5 }}>{body}</div>
          </button>
        ))}
      </div>
      <table data-testid="prod-way-compare" style={{ width: "100%", borderCollapse: "collapse", fontFamily: MONO, fontSize: 11 }}>
        <thead>
          <tr style={{ color: C.dim, textAlign: "left" }}>
            <th style={{ padding: "6px 8px", fontWeight: 400 }} />
            <th style={{ padding: "6px 8px", fontWeight: 400 }}>wrap in code</th>
            <th style={{ padding: "6px 8px", fontWeight: 400 }}>proxy</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([k, a, b]) => (
            <tr key={k} style={{ borderTop: `1px solid ${C.line}` }}>
              <td style={{ padding: "6px 8px", color: C.mut }}>{k}</td>
              <td style={{ padding: "6px 8px", color: C.text }}>{a}</td>
              <td style={{ padding: "6px 8px", color: C.text }}>{b}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 12 }}>
        1 production agent free · Team $299 / month: 10 agents ·{" "}
        <span style={{ color: C.steel, cursor: "pointer" }} onClick={() => navigate("pricing?for=production")}>pricing →</span>
      </div>
    </div>
  );
}

type ProxyMode = "guard" | "test";

/** The proxy has two jobs: guard the agent's tools (blocks), or a test run
 * (watches, and can break a tool on purpose to catch a made-up answer). */
function ProxyModes({ onWrap, initial }: { onWrap: () => void; initial: ProxyMode }) {
  const [mode, setMode] = useState<ProxyMode>(initial);
  return (
    <>
      <div className="flex gap-2 flex-wrap" style={{ marginBottom: 16 }} data-testid="proxy-modes">
        {([
          ["guard", "Guard", "blocks — always on, in production"],
          ["test", "Test run", "watches — and breaks a tool on purpose"],
        ] as [ProxyMode, string, string][]).map(([id, title, body]) => (
          <button key={id} onClick={() => setMode(id)} data-testid={`proxy-mode-${id}`}
            aria-pressed={mode === id}
            style={{ flex: "1 1 200px", textAlign: "left", cursor: "pointer", background: mode === id ? C.panel : "none",
                     color: C.text, border: `1px solid ${mode === id ? C.steel : C.line}`, borderRadius: 8,
                     padding: "8px 12px", fontFamily: MONO }}>
            <span style={{ fontSize: 12.5, fontWeight: 700 }}>{title}</span>
            <span style={{ fontSize: 10.5, color: C.mut, marginLeft: 8 }}>{body}</span>
          </button>
        ))}
      </div>
      {mode === "guard" ? <GuardOnboarding /> : <ProxyOnboarding onWrap={onWrap} />}
    </>
  );
}

function WaySwitch({ way, setWay }: { way: ProdWay; setWay: (w: ProdWay | null) => void }) {
  return (
    <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginBottom: 14 }}>
      {way === "code" ? "wrapping it in code" : "a proxy in front of its tools"} ·{" "}
      <span data-testid="prod-way-back" style={{ color: C.steel, cursor: "pointer" }} onClick={() => setWay(null)}>
        compare the two ways
      </span>
    </div>
  );
}

/** "Both" is one way in walked in two parts: say so, and let either be reopened. */
function BothSteps({ at, onStep }: { at: 1 | 2; onStep: (n: 1 | 2) => void }) {
  const nodes = useQuery({ queryKey: ["nodes"], queryFn: api.nodes, refetchInterval: 3000 });
  const codingIn = (nodes.data ?? []).some((n) => isCodingSession(n.node_id) && !/-example-/.test(n.node_id));
  const step = (n: 1 | 2, label: string, done: boolean) => (
    <button key={n} onClick={() => onStep(n)} data-testid={`both-step-${n}`} aria-current={at === n ? "step" : undefined}
      style={{ background: "none", border: "none", padding: 0, cursor: "pointer", display: "flex", alignItems: "center",
               gap: 8, fontFamily: MONO, fontSize: 12, color: at === n ? C.text : C.mut }}>
      <span style={{ width: 22, height: 22, borderRadius: 11, display: "inline-flex", alignItems: "center",
                     justifyContent: "center", fontSize: 11,
                     border: `1px solid ${done ? C.green : at === n ? C.steel : C.line}`,
                     color: done ? C.green : at === n ? C.steel : C.dim }}>{done ? "✓" : n}</span>
      {label}
    </button>
  );
  return (
    <div data-testid="both-steps" className="flex items-center gap-3 flex-wrap" style={{ marginBottom: 18 }}>
      {step(1, "Coding agents", codingIn)}
      <span style={{ color: C.dim, fontFamily: MONO }}>→</span>
      {step(2, "Production agent", false)}
    </div>
  );
}

function ProxyOnboarding({ onWrap }: { onWrap: () => void }) {
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
  const connect = useApp((s) => s.connect);
  // what each tool does — the shared record (store.toolPolicy): the test run
  // is armed with it, so it says which calls policy would have refused
  const policy = useApp((s) => s.toolPolicy);
  const setToolClass = useApp((s) => s.setToolClass);
  const loadExample = (set: Tool[]) => {
    setTools(set);
    for (const t of set) setToolClass(t.name, { url: t.url, ...(TOOL_CLASSES[t.name] ?? {}) });
  };
  const missingUrl = tools.some((t) => !t.url);

  const preflight = useMutation({ mutationFn: api.proxyPreflight });

  const finish = (): void => {
    connect("proxy", tools);
    navigate("catch");
  };

  const addTool = () => {
    const name = draftName.trim();
    const url = draftUrl.trim();
    if (!name || !url) return;
    if (tools.some((t) => t.name === name)) return;
    setTools([...tools, { name, url }]);
    setToolClass(name, { url });
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
      <Coach id="onboarding" title="A proxy in front of your agent's tools">
        Three steps: list the tools your agent calls, point it at the proxy instead
        (its credentials pass through untouched, nothing else changes), and check each
        one answers. Then run a test: the proxy watches every call and can break a
        tool on purpose to see whether your agent admits it.
      </Coach>
      <div data-testid="test-run-intro" style={{ fontSize: 13.5, color: C.mut, lineHeight: 1.6, marginBottom: 14 }}>
        <span style={{ color: C.text }}>The same test, on your agent.</span> List its tools, point it at the
        proxy, then we break one on purpose and check what your agent says about it.
      </div>
      <div className="flex gap-6 mb-8">
        <StepDot n={1} label="its tools" /><StepDot n={2} label="point it here" /><StepDot n={3} label="check, then test" />
      </div>

      {step === 1 && (
        <>
          <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 4px" }}>What tools does your agent use?</h1>
          <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, marginBottom: 14 }}>The proxy sits in front of these. Your agent's credentials pass through untouched.</div>

          {tools.length === 0 ? (
            <div className="p-8 flex flex-col items-center gap-3" style={{ background: C.panel, border: `1px dashed ${C.line}`, borderRadius: 8 }}>
              <Tooltip content="Fills in a realistic 3-tool sample (search / report / query) so you can walk the flow without typing endpoints.">
                <button onClick={() => loadExample(TOOLS)} style={btn({ color: C.steel, borderColor: C.steel, fontSize: 12 })}>
                  <Plug size={13} /> Load example tools
                </button>
              </Tooltip>
              <ScanTools testId="proxy-scan" onFound={(found) => setTools((prev) => [
                ...prev, ...found.filter((f) => !prev.some((t) => t.name === f.id))
                  .map((f) => ({ name: f.id, url: policy[f.id]?.url ?? "" }))])} />
              <span style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>
                loads a sample set · or find them in your agent's code · or add each tool by hand below · or paste an MCP config further down · or{" "}
                <span
                  role="button"
                  onClick={() => loadExample(MOCK_TOOLS)}
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
                      {t.url ? (
                        <span style={{ fontFamily: MONO, fontSize: 11, color: C.dim, flex: 1 }}>{t.url}</span>
                      ) : (
                        <input placeholder="where it lives: https://… ⏎" aria-label={`where ${t.name} lives`}
                          onKeyDown={(e) => {
                            const v = e.currentTarget.value.trim();
                            if (e.key === "Enter" && v) {
                              setTools(tools.map((x) => (x.name === t.name ? { ...x, url: v } : x)));
                              setToolClass(t.name, { url: v });
                            }
                          }}
                          style={{ flex: 1, background: C.bg, border: `1px solid ${C.amber}`, borderRadius: 4, color: C.text,
                                   fontFamily: MONO, fontSize: 11.5, padding: "5px 8px", outline: "none" }} />
                      )}
                      <select value={policy[t.name]?.klass ?? "READ"} aria-label={`what ${t.name} does`}
                        onChange={(e) => setToolClass(t.name, { klass: e.target.value as ToolKlass })}
                        style={{ background: C.bg, color: C.text, border: `1px solid ${C.line}`, borderRadius: 4,
                                 fontFamily: MONO, fontSize: 11, padding: "3px 6px" }}>
                        {KLASS_LABEL.map((k) => <option key={k.id} value={k.id}>{k.label}</option>)}
                      </select>
                      {(policy[t.name]?.klass ?? "READ") === "READ" && (
                        <label title="brings in outside text (web, email, documents)"
                          style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, display: "flex", alignItems: "center", gap: 4, cursor: "pointer" }}>
                          <input type="checkbox" checked={policy[t.name]?.outside ?? false}
                            onChange={(e) => setToolClass(t.name, { outside: e.target.checked })} />
                          outside text
                        </label>
                      )}
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
              <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 8, lineHeight: 1.6 }}>
                what each tool does lets the test say which calls policy would have refused · <ScanTools testId="proxy-scan-more" label="find more in your code" onFound={(found) => setTools((prev) => [
                  ...prev, ...found.filter((f) => !prev.some((t) => t.name === f.id))
                    .map((f) => ({ name: f.id, url: policy[f.id]?.url ?? "" }))])} />
              </div>
              <button onClick={() => setStep(2)} disabled={missingUrl} className="mt-4"
                style={btn({ color: missingUrl ? C.dim : C.text, borderColor: missingUrl ? C.line : C.steel, fontSize: 12.5 })}>
                {missingUrl ? "add where each tool lives first" : <>Continue <ArrowRight size={13} /></>}
              </button>
            </>
          )}
          {/* MCP, second: most agents' tools are not an MCP server, and the ones that are come with a config to paste. */}
          <div className="p-3 mt-4" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
            <div style={{ fontSize: 10, fontFamily: MONO, color: C.dim, letterSpacing: "0.1em", marginBottom: 8 }}>
              OR PASTE AN MCP CLIENT CONFIG
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
        </>
      )}

      {step === 2 && (
        <>
          <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 4px" }}>Point your agent at the proxy.</h1>
          <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, marginBottom: 20 }}>In your agent's config, replace each tool's address with these. Nothing else changes.</div>
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
            these addresses answer during a test run; outside one they return 503
          </div>
          <button onClick={() => setStep(3)} className="mt-4" style={btn({ color: C.text, borderColor: C.steel, fontSize: 12.5 })}>
            Continue <ArrowRight size={13} />
          </button>
        </>
      )}

      {step === 3 && (
        <>
          <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 4px" }}>Check every tool answers through the proxy.</h1>
          <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, marginBottom: 20 }}>
            A red light now is easy to fix; in the middle of a test it spoils the run.
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
            <Tooltip content="Calls every listed tool through the proxy right now.">
              <button onClick={() => preflight.mutate()} style={btn({ color: C.text, borderColor: C.steel, fontSize: 12.5 })}>
                <RefreshCw size={13} /> {preflight.data || preflight.isError ? "Re-test all" : "Test connections"}
              </button>
            </Tooltip>
            {allGreen && (
              <button onClick={finish} style={btn({ color: C.bg, background: C.green, borderColor: C.green, fontSize: 12.5, fontWeight: 700 })}>
                <Zap size={13} /> Run a first test <ArrowRight size={13} />
              </button>
            )}
          </div>
          <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 12, lineHeight: 1.6,
                        border: `1px solid ${C.line}`, borderRadius: 6, padding: "8px 10px" }}>
            the proxy sees your tool traffic to observe it; credentials pass through untouched and are never
            stored, and only observations are kept, never raw bodies. Self-host it to keep every byte on your
            own machines.
            <br />a test run only watches. To refuse calls, pick Guard above — or, to reach the model too,{" "}
            <span data-testid="proxy-to-wrap" style={{ color: C.steel, cursor: "pointer" }} onClick={onWrap}>wrap it in code →</span>
          </div>
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
