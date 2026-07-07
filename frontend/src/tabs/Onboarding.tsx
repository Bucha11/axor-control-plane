// Get started: tools → point agent → connection check (onboarding mockup).
// Step 3 is wired to the real proxy preflight endpoint via react-query.
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { ArrowRight, Check, Circle, Copy, Plug, RefreshCw, Zap } from "lucide-react";
import { api } from "../api";
import { navigate } from "../router";
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

type RowState = "idle" | "testing" | "ok" | "fail";

export default function Onboarding() {
  const [step, setStep] = useState(1);
  const [tools, setTools] = useState<Tool[]>([]);
  const [copied, setCopied] = useState<string | null>(null);
  const [draftName, setDraftName] = useState("");
  const [draftUrl, setDraftUrl] = useState("");
  // Adapter path is chosen up front (spec section 2, Axis A): it unlocks the
  // Control plane, taint graph and probe health. Proxy is the default depth.
  const [depth, setDepth] = useState<Extract<ConnectionMode, "proxy" | "adapter">>("proxy");
  const connect = useApp((s) => s.connect);

  const preflight = useMutation({ mutationFn: api.proxyPreflight });

  const finish = (): void => {
    connect(depth, tools);
    navigate("eval");
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
    const entry = preflight.data?.tools[name];
    if (!entry) return "idle";
    return entry.ok ? "ok" : "fail";
  };
  const result = preflight.data;
  const allGreen =
    tools.length > 0 && !!result && tools.every((t) => result.tools[t.name]?.ok);

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
          <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, marginBottom: 20 }}>The proxy will sit in front of these. Auth passes through untouched.</div>
          {tools.length === 0 ? (
            <div className="p-8 flex flex-col items-center gap-3" style={{ background: C.panel, border: `1px dashed ${C.line}`, borderRadius: 8 }}>
              <Tooltip content="Fills in a realistic 3-tool sample (search / report / query) so you can walk the flow without typing endpoints.">
                <button onClick={() => setTools(TOOLS)} style={btn({ color: C.steel, borderColor: C.steel, fontSize: 12 })}>
                  <Plug size={13} /> Load example tools
                </button>
              </Tooltip>
              <span style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>
                loads a sample set — parsing a real MCP manifest is on the roadmap · or add endpoints by hand · or use our mock tools (zero creds)
              </span>
              <div className="w-full">{addRow}</div>
            </div>
          ) : (
            <>
              <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
                {tools.map((t, i) => (
                  <div key={t.name} className="flex items-center gap-3 px-4 py-3" style={{ borderTop: i ? `1px solid ${C.line}` : "none" }}>
                    <span style={{ fontFamily: MONO, fontSize: 13, color: C.text, width: 130 }}>{t.name}</span>
                    <span style={{ fontFamily: MONO, fontSize: 11, color: C.dim, flex: 1 }}>{t.url}</span>
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
              const proxied = `http://127.0.0.1:8401/t/${t.name}/`;
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
                    <span style={{ fontFamily: MONO, fontSize: 11, color: st === "ok" ? C.green : st === "fail" ? C.red : C.dim }}>
                      {st === "ok" ? "reachable · auth passes" : st === "fail" ? failLabel : st === "testing" ? "pinging…" : "untested"}
                    </span>
                  </div>
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
