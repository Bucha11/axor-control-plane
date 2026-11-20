// Guided tour — a spotlight walkthrough of the product on example data. Two
// tours, one per kind of agent on Home (coding agents, production agents): the
// visitor picked one by the tab they were on, and a tour of the other would
// talk about things they did not come for. Hand-rolled like the rest of the
// UI: a dimmed backdrop with a cut-out over the current target (a CSS
// selector), a card with the story in plain words, back / next / skip. Steps
// navigate the hash router themselves; if a target isn't on screen the card
// centers and the story still reads. The last step hands over to the way in.
//
// Starting it loads that side's example data first — the same one-click data
// Home offers — so no stop is an empty page. The step is persisted: a reload
// resumes.
import { useCallback, useEffect, useState } from "react";
import { X } from "lucide-react";
import { api } from "../api";
import { navigate } from "../router";
import { useApp } from "../store";
import { C, MONO } from "../theme";

export type TourSide = "coding" | "production";

interface TourStep {
  route: string;         // hash route this step lives on
  target: string | null; // CSS selector to spotlight; null = centered card
  title: string;
  body: string;
  cta?: { label: string; route: string }; // the last step: the way in
}

const sel = (testId: string) => `[data-testid="${testId}"]`;

const LEARN = "Lost later? “learn” at the top puts a short note on every screen.";

export const TOURS: Record<TourSide, TourStep[]> = {
  coding: [
    {
      route: "govern", target: sel("coding-example-story"), title: "Your coding agents, on one plane",
      body: "Example data: Claude Code and Codex in one repository. Claude Code read a docs page — what it brought back stays marked “from the web”. Codex then tried to run the command that page suggested and was refused at the shell, though Codex never saw the page. Every session shows up here live: pause it, stop it, cap it.",
    },
    {
      route: "pair/{pair}", target: sel("pair-pane"), title: "Pair on a session",
      body: "Open a pair on a teammate's session and send the link: you watch each step as it happens — what it ran, what failed, what it said — and can write the agent a note. It gets the note at its next step.",
    },
    {
      route: "memory", target: sel("memory-preview"), title: "What your agents learned, for the whole team",
      body: "When a command passes — how to run the tests here, which one fails and which works — it is remembered for the repository and given to every agent at the start of its next session. Only what was recorded counts, never what an agent claimed; a command that came from a web page is kept out.",
    },
    {
      route: "health", target: sel("setup-health-latest"), title: "Is your setup making agents worse?",
      body: "A check runs your skills, CLAUDE.md and team memory on tasks that tempt an agent — a script to download, a secret within reach, a failing test. When something got worse since the last check, it names the piece that did it, and you turn it off from here.",
    },
    {
      route: "skill-evals", target: sel("team-skills"), title: "Skills: which help, shared in one click",
      body: "Try a skill before you install it. An eval shows which of your skills help and what they cost. The ones that help, turn on for the team — every developer's agent gets it; turn one off, and every agent is refused it.",
    },
    {
      route: "evidence", target: sel("evidence-list"), title: "Incidents",
      body: "When an agent says the tests passed and they failed, or runs what a web page told it to, it lands here — what really happened next to what the agent said. Settings → alerts sends it to e-mail or a webhook as it happens.",
    },
    {
      route: "trees/tree-example-coding", target: sel("example-intro"), title: "A team of agents on one task",
      body: "Claude Code writes a plan, five Codex readers look through the code, Claude Code does the work. A finding about a file its reader never opened is marked unverified, and an instruction found in a web page never reaches the agent that writes code.",
    },
    {
      route: "settings?tab=connect", target: sel("settings-tabs"), title: "Your team",
      body: "Invite your team with a link, make the key their agents report with, choose what alerts you. Billing shows what a month costs: Coding is per developer whose agents ran.",
    },
    {
      route: "home?for=coding", target: sel("home-connect-coding"), title: "Now yours",
      body: `One install, no code change: every session shows up here. Free for 1 developer and one kind of agent — Claude Code or Codex; a team, team memory and shared skills are on Coding. ${LEARN}`,
      cta: { label: "Connect your coding agents →", route: "connect?for=coding" },
    },
  ],
  production: [
    {
      route: "govern", target: sel("fleet-example-story"), title: "Your agents, live",
      body: "Example data: four production agents. Read what happened to each and what Axor did about it — every line opens that agent.",
    },
    {
      route: "govern", target: '[data-tour="spawn"]', title: "Pause, stop, cap",
      body: "Every tool call an agent makes is checked before it runs. From here you pause an agent, stop it, or cap how many more calls it may make — it obeys on its next call.",
    },
    {
      route: "policy", target: sel("wrap-dropzone"), title: "Policy: what each tool may do",
      body: "Drop your agent's code: we find its tools and guess what each one does — reads, writes, sends something out, and how much harm a call can do. You correct the guess. That is what gets enforced, not the prompt.",
    },
    {
      route: "chaos", target: null, title: "Chaos tests",
      body: "Break a tool on purpose — make it fail, time out or answer nothing — on the example, on a live agent's next run, or on a schedule, and see whether the agent tells you or makes something up.",
    },
    {
      route: "evidence", target: sel("evidence-list"), title: "Incidents",
      body: "When an agent says a tool worked and it didn't, a dangerous call goes through, or it tries to send out text it read on the web — it lands here, and Settings → alerts pages you as it happens.",
    },
    {
      route: "explain/ex_block", target: '[data-tour="replay-picker"]', title: "Replay: how it happened",
      body: "Find the step — by when, which agent, what it did — and see where it came from. “What if…” replays the same run under different rules — no model is called.",
    },
    {
      route: "prevent", target: '[data-tour="regression-run"]', title: "Safe to ship?",
      body: "Before you change the rules, every case you kept is replayed under the new ones: attacks must stay blocked, normal work must keep passing.",
    },
    {
      route: "settings?tab=security", target: sel("security-posture"), title: "How this plane is protected",
      body: "Every wall — sign-in, signed commands, sealed tool credentials, how long runs are kept — up or down, with how to raise the ones that are down. Give each agent a key of its own so one can't speak for its neighbours.",
    },
    {
      route: "home?for=production", target: sel("home-connect-production"), title: "Now yours",
      body: `Wrap your agent in code, or put a proxy in front of its tools — free for one agent. ${LEARN}`,
      cta: { label: "Govern my production agent →", route: "connect?for=production" },
    },
  ],
};

// the example pair session the coding tour opens (its token is made fresh)
const PAIR_KEY = "axor-tour-pair";
function routeOf(step: TourStep): string {
  if (!step.route.includes("{pair}")) return step.route;
  const token = typeof localStorage === "undefined" ? "" : localStorage.getItem(PAIR_KEY) ?? "";
  return token ? step.route.replace("{pair}", token) : "pair";
}

// Shared launcher: load the side's example data, then start on its first stop.
export function useStartTour(): (side?: TourSide) => void {
  const connect = useApp((s) => s.connect);
  const setTourStep = useApp((s) => s.setTourStep);
  const setTourSide = useApp((s) => s.setTourSide);
  const stored = useApp((s) => s.tourSide);
  const markLearnSeen = useApp((s) => s.markLearnSeen);
  const setJourney = useApp((s) => s.setJourney);
  return useCallback((side?: TourSide) => {
    const which = side ?? stored;
    markLearnSeen();
    setTourSide(which);
    setJourney(which);
    const seeds = which === "coding"
      ? [api.seedCoding().then(() => Promise.allSettled([
          api.seedPair().then((p) => localStorage.setItem(PAIR_KEY, p.token)),
          api.seedMemory(), api.seedSetupHealth(), api.seedSkillEval(), api.seedTeamSkill()]))]
      : [api.seedFleet(), api.seedAdapterRuns(), api.seedProdIncident()];
    // example data is idempotent; a failed seed still leaves a readable tour
    void Promise.allSettled(seeds).then(() => {
      connect("demo");
      setTourStep(0);
    });
  }, [stored, connect, setTourStep, setTourSide, markLearnSeen, setJourney]);
}

const CARD_W = 340;
const CARD_H_GUESS = 190; // for above/below placement only

export default function Tour() {
  const stepIdx = useApp((s) => s.tourStep);
  const setTourStep = useApp((s) => s.setTourStep);
  const TOUR = TOURS[useApp((s) => s.tourSide)];
  const step = stepIdx == null ? TOUR[0] : TOUR[Math.min(stepIdx, TOUR.length - 1)];
  const active = stepIdx != null;
  const [rect, setRect] = useState<DOMRect | null>(null);

  // Walk the router to the step's surface. A persisted index beyond the tour
  // (an older, longer tour definition) ends cleanly instead of running off.
  useEffect(() => {
    if (!active) return;
    if (stepIdx != null && stepIdx >= TOUR.length) {
      setTourStep(null);
      return;
    }
    const route = routeOf(step);
    const want = `#/${route}`;
    if (window.location.hash !== want) navigate(route);
  }, [active, stepIdx]); // eslint-disable-line react-hooks/exhaustive-deps

  // Find and track the target (poll briefly — the tab may still be mounting).
  useEffect(() => {
    if (!active) return;
    setRect(null);
    const target = step.target;
    if (!target) return;
    let tries = 0;
    let timer = 0;
    let found = false;
    const measure = () => {
      const el = document.querySelector(target);
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
      const el = document.querySelector(target);
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

  if (!active || stepIdx == null || stepIdx >= TOUR.length) return null;

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
        {step.cta && (
          <button
            onClick={() => { const r = step.cta!.route; end(); navigate(r); }}
            style={{ width: "100%", marginBottom: 10, background: C.steel, border: `1px solid ${C.steel}`, borderRadius: 5, color: C.bg, fontFamily: MONO, fontSize: 11.5, fontWeight: 700, padding: "7px 14px", cursor: "pointer" }}
          >
            {step.cta.label}
          </button>
        )}
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
              style={step.cta
                ? { background: "none", border: `1px solid ${C.line}`, borderRadius: 5, color: C.mut, fontFamily: MONO, fontSize: 11, padding: "5px 12px", cursor: "pointer" }
                : { background: C.steel, border: `1px solid ${C.steel}`, borderRadius: 5, color: C.bg, fontFamily: MONO, fontSize: 11, fontWeight: 700, padding: "5px 14px", cursor: "pointer" }}
            >
              {last ? "done" : "next →"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
