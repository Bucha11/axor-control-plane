// Home — the funnel entry (spec section 3). Two intents, kept separate: "what
// does this even do?" (the Demo, a standalone recorded showcase) and "how does
// MY agent behave?" (Get Started). Depth opens on demand; nothing is pushed.
import { useState } from "react";
import { ArrowRight, ChevronDown, GraduationCap, Play, X, Zap } from "lucide-react";
import { api } from "../api";
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

/** The first thing someone sees, knowing nothing about Axor. No menu, no
 * terms: one sentence of what goes wrong, the recording showing it happen, and
 * two ways to try it. The app's vocabulary (evidence, catch, explain…) waits
 * until there is something in it to name. */
function FirstVisit() {
  const connect = useApp((s) => s.connect);
  const startTour = useStartTour();
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const seeFleet = async () => {
    setLoading(true);
    setErr(null);
    try {
      await api.seedFleet();
      connect("demo");
      navigate("govern");
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setLoading(false);
    }
  };
  return (
    <div style={{ maxWidth: 640, margin: "0 auto" }} data-testid="first-visit">
      <h1 style={{ fontSize: "clamp(22px, 6vw, 28px)", fontWeight: 700, lineHeight: 1.25, margin: "0 0 10px" }}>
        Decide what your AI agents are allowed to do.
        <br />
        <span style={{ color: C.steel }}>Axor enforces it where they touch your tools.</span>
      </h1>
      <div style={{ fontSize: 14.5, color: C.mut, lineHeight: 1.6, marginBottom: 20 }}>
        Policy — not the prompt — decides what an agent may read, send and run. Watch every
        agent live, stop it, and repair it when its context goes bad.
      </div>

      {/* the ways in come BEFORE the recording: on a phone the recording alone
          fills the first screen */}
      <div className="flex flex-col" style={{ gap: 10, marginBottom: 12 }}>
        <button onClick={() => void seeFleet()} disabled={loading} style={{ ...bigCta(C.green, true) }}>
          <span>
            <span style={{ display: "block", fontWeight: 700 }}>
              {loading ? "Loading the example fleet…" : "See it govern an example fleet"}
            </span>
            <span style={{ display: "block", fontSize: 12, opacity: 0.85, marginTop: 2 }}>
              one click · nothing to install · no account
            </span>
          </span>
          <ArrowRight size={18} />
        </button>
        <button onClick={() => navigate("connect?for=production")} style={{ ...bigCta(C.steel, false) }}>
          <span>
            <span style={{ display: "block", fontWeight: 700 }}>Govern my own agent</span>
            <span style={{ display: "block", fontSize: 12, opacity: 0.85, marginTop: 2 }}>
              wrap it with axor-core · pause, cap, stop and repair it live
            </span>
          </span>
          <ArrowRight size={18} />
        </button>
        <button onClick={() => navigate("connect?for=coding")} style={{ ...bigCta(C.steel, false) }}
          data-testid="home-coding">
          <span>
            <span style={{ display: "block", fontWeight: 700 }}>Govern my coding agents</span>
            <span style={{ display: "block", fontSize: 12, opacity: 0.85, marginTop: 2 }}>
              Claude Code and Codex · two commands · every session on the plane
            </span>
          </span>
          <ArrowRight size={18} />
        </button>
      </div>
      {err && <div style={{ fontFamily: MONO, fontSize: 11, color: C.red, marginBottom: 8 }}>{err}</div>}
      <div style={{ fontSize: 13, color: C.mut, marginBottom: 26, lineHeight: 1.6 }}>
        Not ready to change code?{" "}
        <button
          onClick={() => {
            connect("demo");
            navigate("catch", { auto: "1" });
          }}
          style={{ background: "none", border: "none", padding: 0, color: C.steel, cursor: "pointer", fontSize: 13, textAlign: "left" }}
        >
          See it catch an agent making things up when a tool breaks — no code →
        </button>
      </div>

      <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, marginBottom: 10 }}>
        watch it happen — 25 seconds:
      </div>
      {/* the buttons above are the call to action; the recording needs no third */}
      <DemoStage autoplay cta={false} onTry={() => navigate("connect")} />

      <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim, marginTop: 18 }}>
        open source ·{" "}
        <button onClick={startTour} style={{ background: "none", border: "none", padding: 0, color: C.steel, cursor: "pointer", fontFamily: MONO, fontSize: 11 }}>
          Take the tour
        </button>{" "}
        — 60 seconds through the whole app
      </div>
    </div>
  );
}

function bigCta(color: string, filled: boolean): React.CSSProperties {
  return {
    display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12,
    width: "100%", textAlign: "left", cursor: "pointer", borderRadius: 10,
    padding: "14px 18px", fontFamily: "Inter, system-ui, sans-serif", fontSize: 15,
    border: `1px solid ${color}`, background: filled ? color : "none",
    color: filled ? C.bg : color,
  };
}

export default function Home() {
  const { mode } = useApp((s) => s.connection);
  if (!isConnected(mode)) return <FirstVisit />;
  return <ConnectedHome />;
}

function ConnectedHome() {
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
        Decide what your AI agents are allowed to do.
        <br />
        <span style={{ color: C.mut }}>Then prove it holds.</span>
      </h1>
      <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.dim, marginBottom: 28 }}>
        policy at the tool boundary, enforced on the node — and every catch kept as a replayable test
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
                govern · watch · repair · prove — the whole loop in ~25 seconds
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
                  : "Wrap your agent as an axor-core Invokable for full governance — this lets you govern it live."}
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
