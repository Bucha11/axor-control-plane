// Standalone demo landing (demo-landing mockup): a recorded trace replayed
// deterministically on an SVG stage — no live model, same outcome every time.
import React, { useState, useEffect } from "react";
import { Play, RotateCcw, ArrowRight, Shield } from "lucide-react";
import { C, MONO } from "../theme";

// Page + stage backgrounds are the mockup's own darker shades (not in the shared palette).
const PAGE_BG = "#0E1216";
const STAGE_BG = "#11151A";

// ---- fixed, deterministic scene (spec §4: recorded, reproducible every time) ----
const N = {
  user:   { x: 70,  y: 150, label: "you" },
  agent:  { x: 300, y: 150, label: "agent" },
  email:  { x: 560, y: 60,  label: "email.read" },
  search: { x: 560, y: 150, label: "web_search" },
  slack:  { x: 560, y: 240, label: "slack.post" },
} as const;
type NodeId = keyof typeof N;
const path = (a: NodeId, b: NodeId) => `M ${N[a].x} ${N[a].y} L ${N[b].x} ${N[b].y}`;

// ── The single-agent story: the product's whole loop on one recording ────────
// One recorded run, replayed deterministically — no live model, same outcome
// every time. It used to end at "export DENIED at the sink", which is the LAST
// rung (live governance, adapter depth) and skipped what the product is for:
// catching the discrepancy, explaining it, and turning it into a test. Now it
// walks the loop in order, and the cards it leaves behind ARE the summary.
type Phase = "catch" | "explain" | "prevent" | "govern";
const PHASES: { id: Phase; label: string }[] = [
  { id: "catch", label: "catch" },
  { id: "explain", label: "explain" },
  { id: "prevent", label: "prevent" },
  { id: "govern", label: "govern" },
];

interface Step {
  dur: number;
  phase: Phase;
  cap: string;
  pulse?: [NodeId, NodeId];
  color?: string;
  mark?: "taint" | "fail" | "claim" | "evidence" | "explain" | "prevent" | "blocked" | "done";
}

const SCRIPT: Step[] = [
  { dur: 1400, phase: "catch", pulse: ["user", "agent"], color: C.steel, cap: "task: “summarize this week and post it to slack”" },
  { dur: 1200, phase: "catch", pulse: ["agent", "email"], color: C.steel, cap: "the agent reads the inbox…" },
  { dur: 1200, phase: "catch", pulse: ["email", "agent"], color: C.amber, mark: "taint", cap: "…external content comes back — tainted" },
  { dur: 1200, phase: "catch", pulse: ["agent", "search"], color: C.steel, cap: "it looks up this week’s rates…" },
  { dur: 1600, phase: "catch", mark: "fail", cap: "we break the tool on purpose: web_search times out, 0 bytes" },
  { dur: 1900, phase: "catch", mark: "claim", cap: "the agent answers anyway — with a number it never got" },
  { dur: 2300, phase: "catch", mark: "evidence", cap: "tool said nothing, agent said 0.25% → an EvidenceCase" },
  { dur: 2600, phase: "explain", mark: "explain", cap: "replayed step by step: the 0.25% has no source" },
  { dur: 2600, phase: "prevent", mark: "prevent", cap: "pinned as must-block; config v2 replayed against it: blocked" },
  { dur: 1500, phase: "govern", pulse: ["agent", "slack"], color: C.red, cap: "live, the agent tries to post it…" },
  { dur: 1900, phase: "govern", mark: "blocked", cap: "…and the export is denied at the sink" },
  { dur: 99999, phase: "govern", mark: "done", cap: "" },
];
const IDLE: Step = { dur: 0, phase: "catch", cap: "" };

// ── Two-tree containment hero (spec v2 decision v2-18; mockups/v2) ────────────
// One recorded fault, replayed over both topologies — deterministic, no live
// model. Left: ungoverned, the lie reaches the export. Right: governed, denied
// at the boundary. This is the multi-agent lead asset; the single-agent split
// lives on the second screen below.
const TREE = [
  { id: "scraper", y: 200 },
  { id: "researcher", y: 120 },
  { id: "orchestrator", y: 40 },
] as const;

const TREE_STEPS = [
  { dur: 1500, cap: "fault injected at the leaf: web_search → ToolError(timeout)", touch: 0 },
  { dur: 1500, cap: "scraper fabricates a result instead of reporting the failure", touch: 0 },
  { dur: 1500, cap: "the fabrication (tainted) is delegated upward — labels ride with the value", touch: 1 },
  { dur: 1500, cap: "researcher folds it into its answer; taint carried, not laundered", touch: 2 },
  { dur: 1400, cap: "…and the answer reaches the export boundary", touch: 2, exportReach: true },
  { dur: 99999, cap: "", verdict: true, touch: 2 },
] as const;

function TwoTreeSecond() {
  const [playing, setPlaying] = useState(false);
  const [i, setI] = useState(-1);

  useEffect(() => {
    if (!playing || i >= TREE_STEPS.length - 1) return;
    const t = setTimeout(() => setI((x) => x + 1), i < 0 ? 250 : TREE_STEPS[i].dur);
    return () => clearTimeout(t);
  }, [playing, i]);

  const cur = i >= 0 ? TREE_STEPS[i] : null;
  const touched = cur ? cur.touch : -1;
  const verdict = i >= 0 && "verdict" in TREE_STEPS[i] && (TREE_STEPS[i] as { verdict?: boolean }).verdict === true;
  const exportReach = i >= 0 && TREE_STEPS.slice(0, i + 1).some((s) => "exportReach" in s);

  const Tree = ({ governed }: { governed: boolean }) => {
    const contained = verdict && governed;
    const escaped = verdict && !governed;
    return (
      <div style={{ flex: 1, background: STAGE_BG, border: `1px solid ${contained ? C.green : escaped ? C.red : C.line}`, borderRadius: 10, overflow: "hidden" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "8px 12px", borderBottom: `1px solid ${C.line}` }}>
          <span style={{ fontFamily: MONO, fontSize: 11, color: governed ? C.green : C.mut, fontWeight: 700, display: "flex", alignItems: "center", gap: 6 }}>
            <Shield size={12} color={governed ? C.green : C.dim} /> {governed ? "GOVERNED" : "UNGOVERNED"}
          </span>
          {verdict && (
            <span style={{ fontFamily: MONO, fontSize: 10, fontWeight: 700, color: governed ? C.green : C.red }}>
              {governed ? "CONTAINED" : "FABRICATION ESCAPED"}
            </span>
          )}
        </div>
        <svg viewBox="0 0 150 250" style={{ width: "100%", display: "block", maxHeight: 230 }}>
          {TREE.slice(0, -1).map((n, idx) => (
            <line key={n.id} x1="60" y1={n.y} x2="60" y2={TREE[idx + 1].y}
              stroke={touched > idx ? C.amber : C.line} strokeWidth="1.5" />
          ))}
          <line x1="60" y1={TREE[2].y} x2="60" y2="6"
            stroke={exportReach ? (contained ? C.green : C.red) : C.line}
            strokeWidth="1.5" strokeDasharray={contained ? "4 3" : "0"} />
          {contained && (
            <g>
              <circle cx="60" cy="18" r="10" fill={STAGE_BG} stroke={C.green} strokeWidth="1.5" />
              <text x="60" y="22" textAnchor="middle" fontSize="10">🛡</text>
            </g>
          )}
          {escaped && (
            <text x="60" y="14" textAnchor="middle" fill={C.red} fontSize="8" fontFamily={MONO} fontWeight="700">→ SLACK</text>
          )}
          {TREE.map((n, idx) => {
            const hot = touched >= idx && i >= 0;
            const ring = !hot ? C.line : governed ? (verdict && idx === 2 ? C.green : C.amber) : C.red;
            return (
              <g key={n.id}>
                <circle cx="60" cy={n.y} r="15" fill={STAGE_BG} stroke={ring} strokeWidth="1.5"
                  style={hot && !governed ? { filter: `drop-shadow(0 0 5px ${ring})` } : {}} />
                <text x="82" y={n.y + 4} fill={C.mut} fontSize="9" fontFamily={MONO}>{n.id}</text>
              </g>
            );
          })}
          {i >= 0 && !verdict && (
            <text x="60" y={TREE[0].y + 30} textAnchor="middle" fill={C.red} fontSize="8" fontFamily={MONO}>ToolError</text>
          )}
        </svg>
      </div>
    );
  };

  return (
    <div>
      <h2 style={{ fontSize: 21, fontWeight: 650, lineHeight: 1.3, margin: "0 0 8px" }}>
        Running a tree of agents? One bad tool call, three agents.{" "}
        <span style={{ color: C.mut }}>Watch the lie spread — then watch it stop.</span>
      </h2>
      <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.dim, marginBottom: 20 }}>
        same recorded fault, replayed over both topologies — deterministic, no live model
      </div>

      <div style={{ display: "flex", gap: 12 }}>
        <Tree governed={false} />
        <Tree governed={true} />
      </div>

      <div style={{ marginTop: 12, background: STAGE_BG, border: `1px solid ${C.line}`, borderRadius: 10, padding: "10px 16px", minHeight: 44, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        {!playing ? (
          <button onClick={() => { setPlaying(true); setI(-1); }}
            style={{ display: "flex", alignItems: "center", gap: 8, background: C.steel, border: "none", borderRadius: 5, color: C.bg, fontFamily: MONO, fontSize: 12.5, fontWeight: 700, padding: "8px 18px", cursor: "pointer" }}>
            <Play size={14} /> Run the recording
          </button>
        ) : verdict ? (
          <span style={{ fontFamily: MONO, fontSize: 12, color: C.text, animation: "fadeUp .4s ease both" }}>
            Same fabrication in both. Left: it reached Slack. Right:{" "}
            <span style={{ color: C.green }}>denied at the export boundary</span> — the agent failed honestly instead of lying.
          </span>
        ) : (
          <span key={i} style={{ fontFamily: MONO, fontSize: 12, color: C.mut, animation: "fadeUp .3s ease both" }}>
            {cur?.cap}
          </span>
        )}
        {playing && (
          <button onClick={() => setI(-1)} style={{ background: "none", border: "none", color: C.dim, cursor: "pointer" }}>
            <RotateCcw size={14} />
          </button>
        )}
      </div>

      {verdict && (
        <div style={{ animation: "fadeUp .5s ease both", marginTop: 14, background: STAGE_BG, border: `1px solid ${C.line}`, borderRadius: 10, padding: 16 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
            <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, letterSpacing: "0.08em" }}>CONTAINMENT — one fault, measured at each boundary</span>
            <span style={{ fontFamily: MONO, fontSize: 11, color: C.green, fontWeight: 700 }}>1/1 boundaries held</span>
          </div>
          {([
            ["scraper → researcher", "carried, not laundered", C.amber],
            ["researcher → orchestrator", "carried, not laundered", C.amber],
            ["orchestrator → export", "DENIED — contained here", C.green],
          ] as const).map(([edge, note, col]) => (
            <div key={edge} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "2px 0" }}>
              <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>{edge}</span>
              <span style={{ fontFamily: MONO, fontSize: 10.5, color: col }}>{note}</span>
            </div>
          ))}
          <div style={{ marginTop: 10, paddingTop: 10, borderTop: `1px solid ${C.line}`, display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
            <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>
              systemic outcome: <span style={{ color: C.red }}>fabricated_failure</span> → <span style={{ color: C.green }}>honest_failure</span>
            </span>
            <button onClick={() => { window.location.href = "/"; }}
              style={{ display: "flex", alignItems: "center", gap: 8, background: C.green, border: "none", borderRadius: 5, color: C.bg, fontFamily: MONO, fontSize: 12, fontWeight: 700, padding: "7px 14px", cursor: "pointer" }}>
              Try it on your agents <ArrowRight size={13} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

/** The recorded run on its stage, walking catch → explain → prevent → govern,
 * with a card per step of the loop left behind as it goes. Shared by the
 * standalone demo page and the Home accordion, so the two can never tell
 * different stories. `autoplay` starts it at once — the Home card is opened by
 * someone who just asked to watch it. `onTry` is where the final call to
 * action goes. */
export function DemoStage({ autoplay = false, onTry }: { autoplay?: boolean; onTry: () => void }) {
  const [playing, setPlaying] = useState(autoplay);
  const [step, setStep] = useState(-1);

  useEffect(() => {
    if (!playing || step >= SCRIPT.length - 1) return;
    const t = setTimeout(() => setStep((s) => s + 1), step < 0 ? 200 : SCRIPT[step].dur);
    return () => clearTimeout(t);
  }, [playing, step]);

  const reached = (m: NonNullable<Step["mark"]>) =>
    step >= 0 && SCRIPT.slice(0, step + 1).some((s) => s.mark === m);
  const cur = step >= 0 ? SCRIPT[step] : IDLE;
  const phaseIdx = step >= 0 ? PHASES.findIndex((p) => p.id === cur.phase) : -1;
  const done = reached("done");
  const tainted = reached("taint");
  const failed = reached("fail");
  const claimed = reached("claim");
  const caught = reached("evidence");
  const explained = reached("explain");
  const prevented = reached("prevent");
  const blocked = reached("blocked");

  const Node = ({ id, danger, warn }: { id: NodeId; danger?: boolean; warn?: boolean }) => {
    const n = N[id];
    const ring = danger ? C.red : warn ? C.amber : C.line;
    return (
      <g>
        <circle cx={n.x} cy={n.y} r="22" fill={C.panel} stroke={ring} strokeWidth="1.5"
          style={danger || warn ? { filter: `drop-shadow(0 0 6px ${ring})` } : {}} />
        <text x={n.x} y={n.y + 40} textAnchor="middle" fill={C.mut} fontSize="11" fontFamily={MONO}>{n.label}</text>
      </g>
    );
  };

  const card = (accent: string): React.CSSProperties => ({
    animation: "fadeUp .45s ease both", background: C.panel, border: `1px solid ${C.line}`,
    borderLeft: `3px solid ${accent}`, borderRadius: 8, padding: "12px 14px",
  });
  const label: React.CSSProperties = { fontFamily: MONO, fontSize: 9.5, color: C.dim, letterSpacing: "0.1em", marginBottom: 6 };

  return (
    <>
      <style>{`
        @keyframes fadeUp { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }
        @keyframes blink { 0%,100% { opacity: 1; } 50% { opacity: 0.25; } }
      `}</style>

      {/* where in the loop we are */}
      <div className="flex flex-wrap" style={{ gap: 6, marginBottom: 10 }} aria-label="demo progress">
        {PHASES.map((p, i) => {
          const on = i === phaseIdx && !done;
          const past = done || (phaseIdx > i);
          return (
            <span key={p.id} style={{
              fontFamily: MONO, fontSize: 10.5, letterSpacing: "0.08em", padding: "3px 10px", borderRadius: 20,
              border: `1px solid ${on ? C.steel : past ? C.green : C.line}`,
              color: on ? C.steel : past ? C.green : C.dim,
              background: on ? "rgba(127,168,204,0.10)" : "none",
            }}>
              {past ? "✓ " : ""}{p.label}
            </span>
          );
        })}
      </div>

      {/* the stage */}
      <div style={{ background: STAGE_BG, border: `1px solid ${C.line}`, borderRadius: 10, position: "relative", overflow: "hidden" }}>
        <svg viewBox="0 0 660 300" style={{ width: "100%", display: "block" }}>
          {([["user", "agent"], ["agent", "email"], ["agent", "search"], ["agent", "slack"]] as [NodeId, NodeId][]).map(([a, b]) => (
            <path key={a + b} d={path(a, b)} stroke={C.line} strokeWidth="1" fill="none" />
          ))}

          {/* explain: the edge the number should have come from carried nothing */}
          {explained && !blocked && (
            <g style={{ animation: "fadeUp .4s ease both" }}>
              <path d={path("search", "agent")} stroke={C.red} strokeWidth="1.5" strokeDasharray="4 4" fill="none" />
              <text x="430" y="142" textAnchor="middle" fill={C.red} fontSize="10.5" fontFamily={MONO}>∅ 0 bytes</text>
            </g>
          )}

          {blocked && <path d={path("agent", "slack")} stroke={C.red} strokeWidth="1.5" strokeDasharray="5 4" fill="none" />}

          {playing && cur.pulse && !done && (
            <circle key={step} r="5" fill={cur.color ?? C.steel} style={{ filter: `drop-shadow(0 0 5px ${cur.color ?? C.steel})` }}>
              <animateMotion dur={`${cur.dur / 1000}s`} fill="freeze" path={path(cur.pulse[0], cur.pulse[1])} />
            </circle>
          )}

          {blocked && (
            <g style={{ animation: "fadeUp .4s ease both" }}>
              <circle cx="440" cy="198" r="15" fill={C.panel} stroke={C.red} strokeWidth="1.5" />
              <text x="440" y="203" textAnchor="middle" fontSize="13">🛡</text>
              <text x="440" y="232" textAnchor="middle" fill={C.red} fontSize="10" fontFamily={MONO} fontWeight="700">DENIED</text>
            </g>
          )}

          <Node id="user" />
          <Node id="agent" danger={claimed && !blocked} />
          <Node id="email" warn={tainted} />
          <Node id="search" danger={failed} />
          <Node id="slack" />

          {failed && (
            <text x={N.search.x} y={N.search.y - 32} textAnchor="middle" fill={C.red} fontSize="10.5" fontFamily={MONO}
              style={cur.mark === "fail" ? { animation: "blink 1s ease 2" } : {}}>timeout · 0 bytes</text>
          )}
          {tainted && (
            <text x={N.email.x} y={N.email.y - 32} textAnchor="middle" fill={C.amber} fontSize="10.5" fontFamily={MONO}>tainted</text>
          )}

          {claimed && !explained && (
            <g style={{ animation: "fadeUp .4s ease both" }}>
              <rect x="180" y="55" width="240" height="40" rx="6" fill={C.panel} stroke={C.red} strokeWidth="1" />
              <text x="300" y="72" textAnchor="middle" fill={C.text} fontSize="10.5" fontFamily={MONO}>“Based on the search results,</text>
              <text x="300" y="86" textAnchor="middle" fill={C.text} fontSize="10.5" fontFamily={MONO}>rates rose 0.25%”</text>
            </g>
          )}
        </svg>

        {/* caption bar */}
        <div style={{ borderTop: `1px solid ${C.line}`, padding: "10px 16px", minHeight: 40, display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
          {!playing ? (
            <button onClick={() => { setPlaying(true); setStep(-1); }}
              style={{ display: "flex", alignItems: "center", gap: 8, background: C.steel, border: "none", borderRadius: 5, color: C.bg, fontFamily: MONO, fontSize: 12.5, fontWeight: 700, padding: "8px 18px", cursor: "pointer" }}>
              <Play size={14} /> Run the recording
            </button>
          ) : (
            <span key={step} style={{ fontFamily: MONO, fontSize: 12, color: cur.mark === "blocked" || cur.mark === "evidence" ? C.red : C.mut, animation: "fadeUp .3s ease both" }}>
              {done ? "that is the loop: catch it, explain it, prevent it, govern it." : cur.cap}
            </span>
          )}
          {playing && (
            <button onClick={() => setStep(-1)} aria-label="replay the recording" style={{ background: "none", border: "none", color: C.dim, cursor: "pointer", flexShrink: 0 }}>
              <RotateCcw size={14} />
            </button>
          )}
        </div>
      </div>

      {/* what each step of the loop produced — they stay, so the end is a summary */}
      <div className="flex flex-col" style={{ gap: 8, marginTop: 12 }}>
        {caught && (
          <div style={card(C.red)} data-testid="demo-catch">
            <div style={label}>① CATCH · EVIDENCECASE</div>
            <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) auto minmax(0,1fr)", gap: 10, alignItems: "center" }}>
              <div>
                <div style={{ ...label, marginBottom: 3 }}>THE TOOL RETURNED</div>
                <div style={{ fontFamily: MONO, fontSize: 12, color: C.red }}>web_search · 0 bytes</div>
              </div>
              <div style={{ fontFamily: MONO, fontSize: 18, color: C.red }}>≠</div>
              <div>
                <div style={{ ...label, marginBottom: 3 }}>THE AGENT SAID</div>
                <div style={{ fontFamily: MONO, fontSize: 12, color: C.text }}>“rates rose 0.25%”</div>
              </div>
            </div>
            <div style={{ fontFamily: MONO, fontSize: 11, color: C.red, fontWeight: 700, marginTop: 8 }}>
              FABRICATED TOOL RESULT — a receipt, not a score
            </div>
          </div>
        )}
        {explained && (
          <div style={card(C.steel)} data-testid="demo-explain">
            <div style={label}>② EXPLAIN · DETERMINISTIC REPLAY</div>
            <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, lineHeight: 1.7 }}>
              step 4 <span style={{ color: C.red }}>web_search → ∅</span> · step 5 <span style={{ color: C.text }}>claim “0.25%”</span>
              <br />the value has no source in the trace. no model call — the recording re-gates the same way every time.
            </div>
          </div>
        )}
        {prevented && (
          <div style={card(C.green)} data-testid="demo-prevent">
            <div style={label}>③ PREVENT · REGRESSION CORPUS</div>
            <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, lineHeight: 1.7 }}>
              pinned as <span style={{ color: C.text }}>must-block</span> · candidate config v2 replayed against it:{" "}
              <span style={{ color: C.green, fontWeight: 700 }}>✓ blocked</span>
              <br />a config that lets it through again fails CI before it ships.
            </div>
          </div>
        )}
        {blocked && (
          <div style={card(C.amber)} data-testid="demo-govern">
            <div style={label}>④ GOVERN · LIVE</div>
            <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, lineHeight: 1.7 }}>
              the agent tried to post the made-up number: <span style={{ color: C.red, fontWeight: 700 }}>export DENIED at the sink</span>
            </div>
          </div>
        )}
        {done && (
          <div className="flex items-center justify-between flex-wrap" style={{ gap: 10, animation: "fadeUp .5s ease both", marginTop: 4 }}>
            <span style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>
              our agent, our broken tool. yours takes ~5 minutes, no code change.
            </span>
            <button onClick={onTry}
              style={{ display: "flex", alignItems: "center", gap: 8, background: C.green, border: "none", borderRadius: 5, color: C.bg, fontFamily: MONO, fontSize: 12.5, fontWeight: 700, padding: "8px 16px", cursor: "pointer" }}>
              Catch one on your agent <ArrowRight size={14} />
            </button>
          </div>
        )}
      </div>

      {!caught && (
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 12, fontFamily: MONO, fontSize: 11, color: C.dim }}>
          <Shield size={12} color={C.dim} />
          a recorded run — no live model, same outcome every time
        </div>
      )}
    </>
  );
}

export default function DemoLanding() {
  return (
    <div style={{ minHeight: "100vh", background: PAGE_BG, color: C.text, fontFamily: "Inter, system-ui, sans-serif", display: "flex", flexDirection: "column", alignItems: "center", padding: "48px 20px" }}>

      <div style={{ maxWidth: 680, width: "100%" }}>
        <div style={{ fontFamily: MONO, fontSize: 12, color: C.steel, letterSpacing: "0.1em", marginBottom: 12 }}>AXOR CONTROL PLANE</div>

        {/* HERO: the single-agent story (operator decision: it stays the lead) */}
        <h1 style={{ fontSize: 30, fontWeight: 700, lineHeight: 1.25, margin: "0 0 8px" }}>
          Your agent lies when its tools fail.<br />
          <span style={{ color: C.mut }}>Watch one get caught.</span>
        </h1>
        <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.dim, marginBottom: 20 }}>
          a recorded trace, replayed deterministically — no live model, same outcome every time
        </div>

        <DemoStage onTry={() => { window.location.href = "/"; }} />

        {/* SECOND SCREEN: the multi-agent two-tree containment story */}
        <div style={{ marginTop: 48 }}>
          <TwoTreeSecond />
        </div>

        {/* pricing strip — marketing only, not the full page */}
        <div style={{ marginTop: 40 }}>
          <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, marginBottom: 12 }}>
            Free forever for safety. Paid only for how your org runs it.
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>
            {PRICING.map((p) => (
              <div
                key={p.name}
                style={{
                  flex: "1 1 180px",
                  minWidth: 180,
                  background: STAGE_BG,
                  border: `1px solid ${p.name === "Team" ? C.steel : C.line}`,
                  borderRadius: 8,
                  padding: 14,
                }}
              >
                <div style={{ fontFamily: MONO, fontSize: 13, fontWeight: 700, color: C.text }}>{p.name}</div>
                <div style={{ fontFamily: MONO, fontSize: 12.5, color: C.steel, marginTop: 4 }}>{p.price}</div>
                <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 8, lineHeight: 1.5 }}>{p.who}</div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

// compact pricing shapes for the landing strip (full detail lives on the Pricing tab)
const PRICING = [
  { name: "Free", price: "$0 · open source", who: "individuals · small teams · research/academic" },
  { name: "Team", price: "$250 / env · mo", who: "first company deployments · per environment, not per node" },
  { name: "Enterprise", price: "annual contract", who: "SSO/RBAC · air-gapped fleets · compliance" },
] as const;
