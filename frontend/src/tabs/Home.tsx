// Home — the funnel entry (spec section 3). Two intents, kept separate: "what
// does this even do?" (the Demo, a standalone recorded showcase) and "how does
// MY agent behave?" (Get Started). Depth opens on demand; nothing is pushed.
import { ArrowRight, Play, Zap } from "lucide-react";
import { C, MONO } from "../theme";
import { navigate } from "../router";
import { ConnectionMode, MODE_LABEL, isConnected, useApp } from "../store";

const LADDER: { mode: ConnectionMode; blurb: string }[] = [
  { mode: "demo", blurb: "our mock broken tools · zero creds · one click" },
  { mode: "proxy", blurb: "your own tools · ~5 min · no code change" },
  { mode: "adapter", blurb: "wrap in Invokable · full governance + Control" },
];

export default function Home() {
  const { mode } = useApp((s) => s.connection);
  const connect = useApp((s) => s.connect);

  return (
    <div style={{ maxWidth: 640, margin: "0 auto" }}>
      <div style={{ fontFamily: MONO, fontSize: 12, fontWeight: 700, letterSpacing: "0.14em", marginBottom: 12 }}>
        <span style={{ color: C.steel }}>Eval.</span>{" "}
        <span style={{ color: C.amber }}>Control.</span>{" "}
        <span style={{ color: C.green }}>Protect.</span>
      </div>
      <h1 style={{ fontSize: 26, fontWeight: 700, lineHeight: 1.25, margin: "0 0 8px" }}>
        Your agent lies when its tools fail.
        <br />
        <span style={{ color: C.mut }}>Catch it, then govern it.</span>
      </h1>
      <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.dim, marginBottom: 28 }}>
        the EvidenceCase is the artifact — a reproducible caught discrepancy, not a score
      </div>

      {/* Demo — a showcase, not an onboarding step (spec section 4) */}
      <div
        onClick={() => (window.location.href = "/demo.html")}
        className="flex items-center justify-between p-4"
        style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10, cursor: "pointer", marginBottom: 12 }}
      >
        <div className="flex items-center gap-3">
          <Play size={16} color={C.steel} />
          <div>
            <div style={{ fontFamily: MONO, fontSize: 13, color: C.text }}>Watch the demo</div>
            <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>
              our agent · our faults · recorded, deterministic — ~10 seconds
            </div>
          </div>
        </div>
        <ArrowRight size={15} color={C.mut} />
      </div>

      {/* Get Started — reached on intent */}
      <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, margin: "20px 0 10px" }}>
        or run it on an agent — pick a depth, each sold by value already seen
      </div>
      <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10 }}>
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
              <button
                onClick={() => {
                  connect("demo");
                  navigate("eval", { auto: "1" });
                }}
                style={cta(C.green)}
              >
                <Zap size={13} /> Run demo-mode
              </button>
            ) : (
              <button onClick={() => navigate("get-started")} style={cta(C.steel)}>
                Get started <ArrowRight size={13} />
              </button>
            )}
          </div>
        ))}
      </div>

      {isConnected(mode) && (
        <div className="mt-6" style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>
          connected: {MODE_LABEL[mode]} ·{" "}
          <span style={{ color: C.steel, cursor: "pointer" }} onClick={() => navigate("eval")}>
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
