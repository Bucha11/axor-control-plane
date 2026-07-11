import { useState } from "react";
import { Check, Copy, Circle, RefreshCw, ArrowRight, Plug } from "lucide-react";

const C = {
  bg: "#12161A", panel: "#191F26", panel2: "#141920", line: "#262E37",
  text: "#D2DAE1", mut: "#78848F", dim: "#4C5760",
  red: "#E5484D", amber: "#F2A33C", green: "#46A758", steel: "#7FA8CC",
};
const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";
const btn = (extra = {}) => ({
  display: "flex", alignItems: "center", gap: 6, background: "none", border: `1px solid ${C.line}`,
  borderRadius: 5, color: C.mut, fontFamily: MONO, fontSize: 11.5, padding: "7px 14px", cursor: "pointer", ...extra,
});

const TOOLS = [
  { name: "web_search", url: "https://api.search.example/v1" },
  { name: "send_report", url: "https://slack.example/api/post" },
  { name: "run_query", url: "https://db.internal.example/query" },
];

export default function App() {
  const [step, setStep] = useState(1);
  const [tools, setTools] = useState([]);
  const [copied, setCopied] = useState(null);
  // check: null=untested, "testing", "ok", "fail"
  const [checks, setChecks] = useState({});
  const [fixed, setFixed] = useState(false);

  const runChecks = () => {
    const next = {};
    tools.forEach((t) => (next[t.name] = "testing"));
    setChecks(next);
    setTimeout(() => {
      const res = {};
      tools.forEach((t) => (res[t.name] = t.name === "run_query" && !fixed ? "fail" : "ok"));
      setChecks(res);
    }, 900);
  };
  const allGreen = tools.length > 0 && tools.every((t) => checks[t.name] === "ok");

  const StepDot = ({ n, label }) => (
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

  return (
    <div style={{ minHeight: "100vh", background: C.bg, color: C.text, fontFamily: "Inter, system-ui, sans-serif", padding: "28px 20px" }}>
      <div style={{ maxWidth: 640, margin: "0 auto" }}>
        <div className="flex items-center gap-6 mb-8">
          <span style={{ fontFamily: MONO, fontSize: 14, fontWeight: 700 }}>AXOR<span style={{ color: C.steel }}> CONTROL PLANE</span></span>
          <span style={{ fontFamily: MONO, fontSize: 12, color: C.text, borderBottom: `2px solid ${C.steel}`, paddingBottom: 2 }}>get started</span>
        </div>

        <div className="flex gap-6 mb-8">
          <StepDot n={1} label="tools" /><StepDot n={2} label="point agent" /><StepDot n={3} label="connection check" />
        </div>

        {step === 1 && (
          <>
            <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 4px" }}>What tools does your agent use?</h1>
            <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, marginBottom: 20 }}>The proxy will sit in front of these. Auth passes through untouched.</div>
            {tools.length === 0 ? (
              <div className="p-8 flex flex-col items-center gap-3" style={{ background: C.panel, border: `1px dashed ${C.line}`, borderRadius: 8 }}>
                <button onClick={() => setTools(TOOLS)} style={btn({ color: C.steel, borderColor: C.steel, fontSize: 12 })}>
                  <Plug size={13} /> Import MCP config
                </button>
                <span style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>or add endpoints by hand · or use our mock tools (zero creds)</span>
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
                const proxied = `https://p-7c31.axor.dev/${t.name}`;
                return (
                  <div key={t.name} className="flex items-center gap-3 px-4 py-3" style={{ borderTop: i ? `1px solid ${C.line}` : "none" }}>
                    <span style={{ fontFamily: MONO, fontSize: 12, color: C.mut, width: 130 }}>{t.name}</span>
                    <span style={{ fontFamily: MONO, fontSize: 12, color: C.text, flex: 1 }}>{proxied}</span>
                    <button onClick={() => { setCopied(t.name); setTimeout(() => setCopied(null), 1200); }}
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
                const st = checks[t.name];
                return (
                  <div key={t.name} className="px-4 py-3" style={{ borderTop: i ? `1px solid ${C.line}` : "none" }}>
                    <div className="flex items-center gap-3">
                      {st === "testing"
                        ? <RefreshCw size={12} color={C.steel} className="animate-spin" />
                        : <Circle size={9} fill={st === "ok" ? C.green : st === "fail" ? C.red : C.dim} color={st === "ok" ? C.green : st === "fail" ? C.red : C.dim} />}
                      <span style={{ fontFamily: MONO, fontSize: 13, color: C.text, flex: 1 }}>{t.name}</span>
                      <span style={{ fontFamily: MONO, fontSize: 11, color: st === "ok" ? C.green : st === "fail" ? C.red : C.dim }}>
                        {st === "ok" ? "reachable · auth passes" : st === "fail" ? "401 from upstream" : st === "testing" ? "pinging…" : "untested"}
                      </span>
                    </div>
                    {st === "fail" && (
                      <div className="flex items-center gap-3 mt-2" style={{ paddingLeft: 21 }}>
                        <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>
                          Your agent's credential for run_query was rejected upstream. The proxy forwards auth byte-for-byte — fix the credential in your agent config, then re-test.
                        </span>
                        <button onClick={() => setFixed(true)} style={btn({ padding: "4px 10px", fontSize: 10.5 })}>simulate fix</button>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
            <div className="flex items-center gap-3 mt-4">
              <button onClick={runChecks} style={btn({ color: C.text, borderColor: C.steel, fontSize: 12.5 })}>
                <RefreshCw size={13} /> {Object.keys(checks).length ? "Re-test all" : "Test connections"}
              </button>
              {allGreen && (
                <button style={btn({ color: C.bg, background: C.green, borderColor: C.green, fontSize: 12.5, fontWeight: 700 })}>
                  Run first experiment <ArrowRight size={13} />
                </button>
              )}
            </div>
            {allGreen && (
              <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim, marginTop: 12 }}>
                optional: run a baseline health check first (23 probes, ~1 min) — gives drift comparison later
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
