// Guided tour — a spotlight walkthrough of the whole funnel (catch → replay →
// govern → regress). Hand-rolled like the rest of the UI: a dimmed backdrop with
// a cut-out over the current target (found by its data-tour attribute), a card
// with the story, back / next / skip. Steps navigate the hash router themselves,
// so the tour walks across tabs. If a target isn't on screen (e.g. state
// differs), the card centers and the story still reads.
//
// Starting the tour connects demo-mode (if not connected) and seeds the example
// adapter runs — the same one-click data the UI offers everywhere — so every
// stop shows real content. The step index is persisted: a reload resumes.
import { useCallback, useEffect, useState } from "react";
import { X } from "lucide-react";
import { api } from "../api";
import { navigate } from "../router";
import { isConnected, useApp } from "../store";
import { C, MONO } from "../theme";

interface TourStep {
  route: string;         // hash route this step lives on
  target: string | null; // [data-tour=…] to spotlight; null = centered card
  title: string;
  body: string;
}

export const TOUR: TourStep[] = [
  {
    route: "home", target: null, title: "The 60-second tour",
    body: "Axor catches agents lying when their tools fail, replays any run deterministically, and operates governed agents live. We've connected demo-mode and loaded example data, so every stop on this tour is real — and none of it touches your infrastructure.",
  },
  {
    route: "home", target: "ladder", title: "Three ways in",
    body: "Demo-mode: our scripted agent, mock tools, zero credentials. Proxy: we sit in front of YOUR tools, observe-only, no code change. Adapter: your agent wrapped in axor-core for full governance — that's what unlocks Control.",
  },
  {
    route: "eval", target: "eval-config", title: "Eval — break a tool, catch the lie",
    body: "Pick a tool to deprive and how. The agent runs; we compare what actually happened against what it claimed. The mismatch becomes an EvidenceCase — a reproducible receipt (not a score) that you can replay, share, and export.",
  },
  {
    route: "replay/ex_block", target: "replay-picker", title: "Replay — question any run",
    body: "Every recorded step, scrubbable, coloured by verdict. 'What if…' forks a counterfactual: edit the policy and the recorded trace re-gates deterministically, showing the first step where the outcome diverges. No model call involved.",
  },
  {
    route: "control", target: "spawn", title: "Control — operate live agents",
    body: "Adapter-connected agents appear here as live nodes: pause them, cap their budgets (decrease-only), stop a whole subtree in one command. Try this button — it spawns a REAL governed node that heartbeats and obeys.",
  },
  {
    route: "regression", target: "regression-run", title: "Regression — safe to ship?",
    body: "Pin runs into a two-sided corpus: attacks that must stay blocked, legit flows that must keep passing. Replay the corpus under a candidate config and get a deterministic verdict — governance CI.",
  },
  {
    route: "home", target: "learn", title: "Learn as you go",
    body: "Hover any action for a tooltip. This toggle turns on Learn mode — one short note per screen, dismissible, restorable in Settings → LEARN MODE. That's the tour: break something, read the receipt.",
  },
];

// Shared launcher: normalise state so every stop has content, then start.
export function useStartTour(): () => void {
  const { mode } = useApp((s) => s.connection);
  const connect = useApp((s) => s.connect);
  const setTourStep = useApp((s) => s.setTourStep);
  const markLearnSeen = useApp((s) => s.markLearnSeen);
  return useCallback(() => {
    if (!isConnected(mode)) connect("demo");
    void api.seedAdapterRuns().catch(() => undefined); // idempotent example data
    markLearnSeen();
    navigate("home");
    setTourStep(0);
  }, [mode, connect, setTourStep, markLearnSeen]);
}

const CARD_W = 300;
const CARD_H_GUESS = 190; // for above/below placement only

export default function Tour() {
  const stepIdx = useApp((s) => s.tourStep);
  const setTourStep = useApp((s) => s.setTourStep);
  const step = stepIdx == null ? TOUR[0] : TOUR[Math.min(stepIdx, TOUR.length - 1)];
  const active = stepIdx != null;
  const [rect, setRect] = useState<DOMRect | null>(null);

  // Walk the router to the step's surface.
  useEffect(() => {
    if (!active) return;
    const want = `#/${step.route}`;
    if (window.location.hash !== want) navigate(step.route);
  }, [active, stepIdx]); // eslint-disable-line react-hooks/exhaustive-deps

  // Find and track the target (poll briefly — the tab may still be mounting).
  useEffect(() => {
    if (!active) return;
    setRect(null);
    if (!step.target) return;
    let tries = 0;
    let timer = 0;
    let found = false;
    const measure = () => {
      const el = document.querySelector(`[data-tour="${step.target}"]`);
      if (el) {
        if (!found) {
          found = true;
          el.scrollIntoView({ block: "center" });
        }
        setRect(el.getBoundingClientRect());
      } else if (tries++ < 30) {
        timer = window.setTimeout(measure, 100);
      }
    };
    measure();
    const remeasure = () => {
      const el = document.querySelector(`[data-tour="${step.target}"]`);
      if (el) setRect(el.getBoundingClientRect());
    };
    window.addEventListener("resize", remeasure);
    window.addEventListener("scroll", remeasure, true);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("resize", remeasure);
      window.removeEventListener("scroll", remeasure, true);
    };
  }, [active, stepIdx]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!active || stepIdx == null) return null;

  const last = stepIdx === TOUR.length - 1;
  const end = () => setTourStep(null);

  // Card placement: under the spotlight, above it if that would overflow;
  // centered when there is no target on screen.
  let cardStyle: React.CSSProperties;
  if (rect) {
    const below = rect.bottom + CARD_H_GUESS + 24 < window.innerHeight;
    const left = Math.min(
      Math.max(12, rect.left + rect.width / 2 - CARD_W / 2),
      window.innerWidth - CARD_W - 12,
    );
    cardStyle = below
      ? { top: rect.bottom + 12, left }
      : { top: Math.max(12, rect.top - CARD_H_GUESS - 12), left };
  } else {
    cardStyle = { top: "38%", left: "50%", transform: "translateX(-50%)" };
  }

  return (
    <div aria-label="guided tour" role="dialog" style={{ position: "fixed", inset: 0, zIndex: 100, pointerEvents: "none" }}>
      {/* Backdrop: a spotlight cut-out when we have a target, a plain dim otherwise. */}
      {rect ? (
        <div
          style={{
            position: "fixed",
            top: rect.top - 6, left: rect.left - 6,
            width: rect.width + 12, height: rect.height + 12,
            borderRadius: 8,
            border: `1.5px solid ${C.steel}`,
            boxShadow: "0 0 0 9999px rgba(10,13,16,0.62)",
            transition: "top .25s, left .25s, width .25s, height .25s",
            pointerEvents: "none",
          }}
        />
      ) : (
        <div style={{ position: "fixed", inset: 0, background: "rgba(10,13,16,0.62)" }} />
      )}

      <div
        className="p-4"
        style={{
          position: "fixed", width: CARD_W, zIndex: 101, pointerEvents: "auto",
          background: C.panel, border: `1px solid ${C.steel}`, borderRadius: 10,
          boxShadow: "0 16px 48px rgba(0,0,0,0.55)", ...cardStyle,
        }}
      >
        <div className="flex items-start justify-between gap-2 mb-2">
          <div style={{ fontFamily: MONO, fontSize: 12.5, fontWeight: 700, color: C.steel }}>
            {step.title}
          </div>
          <button
            onClick={end}
            aria-label="skip tour"
            style={{ background: "none", border: "none", color: C.dim, cursor: "pointer", padding: 0 }}
          >
            <X size={14} />
          </button>
        </div>
        <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, lineHeight: 1.65, marginBottom: 12 }}>
          {step.body}
        </div>
        <div className="flex items-center justify-between">
          <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>
            {stepIdx + 1} / {TOUR.length}
          </span>
          <div className="flex gap-2">
            {stepIdx > 0 && (
              <button
                onClick={() => setTourStep(stepIdx - 1)}
                style={{ background: "none", border: `1px solid ${C.line}`, borderRadius: 5, color: C.mut, fontFamily: MONO, fontSize: 11, padding: "5px 12px", cursor: "pointer" }}
              >
                back
              </button>
            )}
            <button
              onClick={() => (last ? end() : setTourStep(stepIdx + 1))}
              style={{ background: C.steel, border: `1px solid ${C.steel}`, borderRadius: 5, color: C.bg, fontFamily: MONO, fontSize: 11, fontWeight: 700, padding: "5px 14px", cursor: "pointer" }}
            >
              {last ? "done" : "next →"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
