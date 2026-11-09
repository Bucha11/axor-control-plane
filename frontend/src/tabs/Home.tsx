// Home — the funnel entry (spec section 3). Two intents, kept separate: "what
// does this even do?" (the Demo, a standalone recorded showcase) and "how does
// MY agent behave?" (Get Started). Depth opens on demand; nothing is pushed.
import { useState } from "react";
import { ArrowRight, ChevronDown, GraduationCap, Play, X, Zap } from "lucide-react";
import { C, MONO } from "../theme";
import { navigate } from "../router";
import { ConnectionMode, MODE_LABEL, isConnected, useApp } from "../store";
import Tooltip from "../components/Tooltip";
import { useStartTour } from "../components/Tour";
import { DemoStage } from "../demo/DemoLanding";

const LADDER: { mode: ConnectionMode; blurb: string }[] = [
  { mode: "demo", blurb: "our mock broken tools · zero creds · one click" },
  { mode: "proxy", blurb: "your own tools · ~5 min · no code change" },
  { mode: "adapter", blurb: "wrap in Invokable · full governance + Control" },
];

export default function Home() {
  const { mode } = useApp((s) => s.connection);
  const connect = useApp((s) => s.connect);
  const learnMode = useApp((s) => s.learnMode);
  const learnSeen = useApp((s) => s.learnSeen);
  const setLearnMode = useApp((s) => s.setLearnMode);
  const markLearnSeen = useApp((s) => s.markLearnSeen);
  // the demo opens in place: it is ten seconds long, and leaving the page for
  // it lost the reader at the one moment they were curious
  const [demoOpen, setDemoOpen] = useState(false);
  const startTour = useStartTour();

  return (
    <div style={{ maxWidth: 640, margin: "0 auto" }}>
      <div style={{ fontFamily: MONO, fontSize: 12, fontWeight: 700, letterSpacing: "0.14em", marginBottom: 12 }}>
        <span style={{ color: C.steel }}>Eval.</span>{" "}
        <span style={{ color: C.amber }}>Control.</span>{" "}
        <span style={{ color: C.green }}>Protect.</span>
      </div>

      {/* First-visit nudge — offered once, never nagged. Turning it on reveals
          coach notes across the app; dismissing just marks it seen. */}
      {!learnMode && !learnSeen && (
        <div
          className="flex items-center justify-between p-3 mb-4 wrap-narrow"
          style={{ background: "rgba(127,168,204,0.06)", border: `1px solid ${C.steel}`, borderRadius: 8 }}
        >
          <div className="flex items-center gap-2" style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut }}>
            <GraduationCap size={14} color={C.steel} />
            {/* one span, so the sentence flows as text instead of three flex columns */}
            <span>New here? Take the 60-second tour, or turn on <span style={{ color: C.steel }}>Learn mode</span> for a note on each screen.</span>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={startTour} style={{ background: C.steel, border: `1px solid ${C.steel}`, borderRadius: 5, color: C.bg, fontFamily: MONO, fontSize: 11, fontWeight: 700, padding: "4px 10px", cursor: "pointer", whiteSpace: "nowrap" }}>
              Take the tour
            </button>
            <button onClick={() => setLearnMode(true)} style={{ background: "none", border: `1px solid ${C.steel}`, borderRadius: 5, color: C.steel, fontFamily: MONO, fontSize: 11, padding: "4px 10px", cursor: "pointer", whiteSpace: "nowrap" }}>
              Learn mode
            </button>
            <button onClick={markLearnSeen} aria-label="dismiss" style={{ background: "none", border: "none", color: C.dim, cursor: "pointer", padding: 0 }}>
              <X size={13} />
            </button>
          </div>
        </div>
      )}
      <h1 style={{ fontSize: 26, fontWeight: 700, lineHeight: 1.25, margin: "0 0 8px" }}>
        Your agent lies when its tools fail.
        <br />
        <span style={{ color: C.mut }}>Catch it, then govern it.</span>
      </h1>
      <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.dim, marginBottom: 28 }}>
        the EvidenceCase is the artifact — a reproducible caught discrepancy, not a score
      </div>

      {/* Demo — a showcase, not an onboarding step (spec section 4). An
          accordion: it opens here and starts playing, instead of leaving for
          a separate page. */}
      <div style={{ background: C.panel, border: `1px solid ${demoOpen ? C.steel : C.line}`, borderRadius: 10, marginBottom: 12, overflow: "hidden" }}>
        <button
          onClick={() => setDemoOpen((v) => !v)}
          aria-expanded={demoOpen}
          aria-controls="home-demo"
          className="flex items-center justify-between p-4"
          style={{ width: "100%", background: "none", border: "none", cursor: "pointer", textAlign: "left", color: C.text, gap: 12 }}
        >
          <span className="flex items-center gap-3">
            <Play size={16} color={C.steel} />
            <span>
              <span style={{ display: "block", fontFamily: MONO, fontSize: 13, color: C.text }}>Watch the demo</span>
              <span style={{ display: "block", fontFamily: MONO, fontSize: 10.5, color: C.dim }}>
                our agent · our faults · recorded, deterministic — ~10 seconds
              </span>
            </span>
          </span>
          <ChevronDown
            size={16}
            color={C.mut}
            style={{ flexShrink: 0, transition: "transform .2s", transform: demoOpen ? "rotate(180deg)" : "none" }}
          />
        </button>
        {demoOpen && (
          <div id="home-demo" style={{ padding: "0 16px 16px" }}>
            <DemoStage autoplay onTry={() => navigate("connect")} />
          </div>
        )}
      </div>

      {/* Get Started — reached on intent */}
      <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, margin: "20px 0 10px" }}>
        or run it on an agent — pick a depth, each sold by value already seen
      </div>
      <div data-tour="ladder" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10 }}>
        {LADDER.map((rung, i) => (
          <div
            key={rung.mode}
            className="flex items-center justify-between px-4 py-3"
            style={{ borderTop: i ? `1px solid ${C.line}` : "none" }}
          >
            <div>
              <div style={{ fontFamily: MONO, fontSize: 13, color: C.text }}>
                {MODE_LABEL[rung.mode]}
              </div>
              <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>{rung.blurb}</div>
            </div>
            {rung.mode === "demo" ? (
              <Tooltip content="Runs our scripted agent against mock broken tools through the real proxy — one click, no credentials. You'll see a fabrication get caught as an EvidenceCase." side="bottom">
                <button
                  onClick={() => {
                    connect("demo");
                    navigate("catch", { auto: "1" });
                  }}
                  style={cta(C.green)}
                >
                  <Zap size={13} /> Run demo-mode
                </button>
              </Tooltip>
            ) : (
              <Tooltip
                content={rung.mode === "proxy"
                  ? "Put the proxy in front of YOUR tools (observe-only, ~5 min, no code change) to run the catch loop on your own agent."
                  : "Wrap your agent as an axor-core Invokable for full governance — this unlocks Govern."}
                side="bottom"
              >
                <button onClick={() => navigate("connect")} style={cta(C.steel)}>
                  Get started <ArrowRight size={13} />
                </button>
              </Tooltip>
            )}
          </div>
        ))}
      </div>

      {isConnected(mode) && (
        <div className="mt-6" style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>
          connected: {MODE_LABEL[mode]} ·{" "}
          <span style={{ color: C.steel, cursor: "pointer" }} onClick={() => navigate("catch")}>
            run an experiment →
          </span>
        </div>
      )}
    </div>
  );
}

function cta(color: string): React.CSSProperties {
  return {
    display: "flex", alignItems: "center", gap: 6, background: "none",
    border: `1px solid ${color}`, borderRadius: 5, color, fontFamily: MONO,
    fontSize: 12, padding: "7px 14px", cursor: "pointer", whiteSpace: "nowrap",
  };
}
