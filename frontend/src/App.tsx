// The application shell. Router-driven, funnel-aware (spec section 3): every
// surface is reachable by a deep link so EvidenceCases, replay moments and share
// permalinks are addressable.
//
// The nav leads with governance, the product; the catch loop is its proof:
//   Agents         (#/govern)   — governed agents: graph, sentinel reputation, context health & repair
//   Policy         (#/policy)   — what each agent may read, send and run
//   Incidents      (#/evidence) — every EvidenceCase caught (tests run at #/catch)
//   Replay         (#/explain)  — step through a run, fork what-ifs
//   Safe to ship?  (#/prevent)  — a policy change against the caught cases
// Older route names stay as aliases, so no shared link breaks.
// It used to read eval · replay · regression · control · more…, which named
// the machinery instead of the job, and hid the first step (get started) and a
// static mock (expert) behind the same "more".
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Circle, GraduationCap, Plug, Settings as SettingsIcon } from "lucide-react";
import { api } from "./api";
import { C, MONO } from "./theme";
import { navigate, useRoute } from "./router";
import { MODE_LABEL, isConnected, isGuestToken, useApp } from "./store";
import GuestSession, { guestTimeLeft } from "./components/GuestSession";
import { track } from "./funnel";
import Tooltip from "./components/Tooltip";
import { codingExampleOnly, fleetExampleOnly } from "./components/CodingOnboarding";
import Tour from "./components/Tour";
import Home from "./tabs/Home";
import Evidence, { useIncidentCount } from "./tabs/Evidence";
import Experiment from "./tabs/Experiment";
import ControlTab from "./tabs/ControlTab";
import ReplayTab from "./tabs/ReplayTab";
import ChaosTests from "./tabs/ChaosTests";
import ReplaySearch from "./tabs/ReplaySearch";
import Onboarding from "./tabs/Onboarding";
import RunControl from "./tabs/RunControl";
import Trees from "./tabs/Trees";
import ConfigBuilder from "./tabs/ConfigBuilder";
import Health from "./tabs/Health";
import Sentinel from "./tabs/Sentinel";
import Regression from "./tabs/Regression";
import Settings, { IdentityLogin } from "./tabs/Settings";
import AuthReturn from "./components/AuthReturn";
import Tabs from "./components/Tabs";
import Pricing from "./tabs/Pricing";
import Skills from "./tabs/Skills";
import SkillEvals from "./tabs/SkillEvals";
import SetupHealth from "./tabs/SetupHealth";
import Pair from "./tabs/Pair";

// Governance first: the product is the policy membrane between an agent and its
// tools. "Incidents" is the proof it works, not the product.
type Journey = "coding" | "production" | "both";
// `for`: which journeys a section belongs to. Coding agents come with their
// rules built in (what Bash or WebFetch does is known), so describing tools,
// catching made-up tool results and checking a rule change are production's;
// a coding team's question before trusting a skill is coding's.
const LOOP: { key: string; label: string; hint: string; for: Journey[] }[] = [
  { key: "govern", label: "Agents", hint: "Your governed agents as a graph: pause, cap, stop — what sentinel sees across sessions, what probe says about their health.", for: ["coding", "production", "both"] },
  { key: "policy", label: "Policy", hint: "What each agent may read, send and run — the rules enforced at the tool boundary.", for: ["production", "both"] },
  { key: "chaos", label: "Chaos tests", hint: "Break a tool on purpose and see what your agent says — on the example, through the proxy, on live agents' next run, or on a schedule.", for: ["production", "both"] },
  { key: "evidence", label: "Incidents", hint: "When an agent said a tool worked and it didn't, said the tests passed when they failed, or tried to send out what came from outside — caught in test runs and in your agents' work.", for: ["coding", "production", "both"] },
  { key: "explain", label: "Replay", hint: "Find the step — by when, which agent, what it did — and see where it came from.", for: ["coding", "production", "both"] },
  { key: "prevent", label: "Safe to ship?", hint: "Check a policy change against every case you've caught, before it ships.", for: ["production", "both"] },
  { key: "skills", label: "Try a skill", hint: "Before installing a skill for Claude Code or Codex: what it asks the agent to do, and what an agent actually does with it.", for: ["coding", "both"] },
  { key: "skill-evals", label: "Skill evals", hint: "Which of your skills help, and what they cost — and the ones that do, shared with the whole team in one click.", for: ["coding", "both"] },
];

/** The sections this person's menu shows: their journey's, or all of them
 * when no journey was chosen or they asked for everything. */
function useMenu(): { items: typeof LOOP; journey: Journey | null; all: boolean } {
  const journey = useApp((s) => s.journey);
  const all = useApp((s) => s.menuAll);
  const items = journey && !all ? LOOP.filter((l) => l.for.includes(journey)) : LOOP;
  return { items, journey, all };
}

/** Under a journey's menu: which one it is, and the way to everything else. */
function MenuScope() {
  const { journey, all } = useMenu();
  const setAll = useApp((s) => s.setMenuAll);
  if (!journey || journey === "both") return null;
  return (
    <div data-testid="menu-scope" style={{ padding: "14px 0 0 16px", fontFamily: MONO, fontSize: 10.5, color: C.dim }}>
      {journey === "coding" ? "coding agents" : "production agents"} ·{" "}
      <button onClick={() => setAll(!all)} style={{ background: "none", border: "none", padding: 0,
        cursor: "pointer", fontFamily: MONO, fontSize: 10.5, color: C.steel }}>
        {all ? "show fewer" : "show everything"}
      </button>
    </div>
  );
}

/** Screens without a tab of their own light up the tab they belong to. */
const PARENT_TAB: Record<string, string> = { catch: "chaos" };

/** Old route heads keep working — they are in shared links and bookmarks. */
const ALIAS: Record<string, string> = {
  eval: "catch",
  replay: "explain",
  regression: "prevent",
  control: "govern",
  health: "govern",
  sentinel: "govern",
  trees: "govern",
  pair: "govern",
  "get-started": "connect",
  "config-builder": "policy",
};
const KNOWN = ["home", "chaos", "evidence", "catch", "explain", "prevent", "govern", "policy", "skills", "skill-evals", "connect", "pricing", "settings"];

function NavLink({ id, label, hint, active }: { id: string; label: string; hint: string; active: boolean }) {
  return (
    <Tooltip content={hint} side="bottom">
      <button
        onClick={() => navigate(id)}
        aria-current={active ? "page" : undefined}
        style={{
          background: "none", border: "none", padding: "2px 0", cursor: "pointer",
          color: active ? C.text : C.dim, fontSize: 13, fontFamily: MONO,
          borderBottom: `2px solid ${active ? C.steel : "transparent"}`,
        }}
      >
        {label}
      </button>
    </Tooltip>
  );
}

function routeKey(segments: string[]): string {
  const head = segments[0] ?? "home";
  return ALIAS[head] ?? head;
}

export default function App() {
  const menu = useMenu();
  const route = useRoute();
  const { mode } = useApp((s) => s.connection);
  const key = routeKey(route.segments);

  // No route: a first visit lands on Home (the funnel entry); once something is
  // connected, on Evidence — what has been caught is the thing to look at.
  useEffect(() => {
    // governed (or the example fleet) → the agents; proxy-only → what it caught
    if (route.segments.length === 0) {
      navigate(!isConnected(mode) ? "home" : mode === "proxy" ? "evidence" : "govern");
    }
  }, [route.segments.length, mode]);

  const head = route.segments[0] ?? "home";
  const runIdParam = route.segments[1];
  const wide = useWide();
  // Someone who has not tried anything yet sees no menu on Home: five tabs of
  // product vocabulary is what made a first visit read as "close and leave".
  // Once a section was opened, Home keeps the menu: the way back in stays.
  const lookedAround = useApp((s) => s.lookedAround);
  const setLookedAround = useApp((s) => s.setLookedAround);
  const inSection = LOOP.some((l) => l.key === (PARENT_TAB[key] ?? key));
  useEffect(() => {
    if (inSection && !lookedAround) setLookedAround(true);
  }, [inSection, lookedAround, setLookedAround]);
  const firstVisit = key === "home" && !isConnected(mode) && !lookedAround;

  // A wide screen gets the navigation as a sidebar: the sections and, under
  // Agents, its views — instead of a row of sections plus a row of views on
  // top of every page. A phone keeps the compact header.
  const sidebar = wide && !firstVisit;
  const governView = head === "health" || head === "sentinel" || head === "trees" || head === "pair"
    ? head : "govern";
  const header = (
    <div className="app-header" style={{ maxWidth: 1200, margin: "0 auto 36px" }}>
      <div className="app-header-left">
        <button
          className="app-logo"
          onClick={() => navigate("home")}
          style={{ background: "none", border: "none", cursor: "pointer", fontFamily: MONO, fontSize: 14, fontWeight: 700, color: C.text, padding: 0, whiteSpace: "nowrap" }}
        >
          AXOR<span className="hide-narrow hide-medium" style={{ color: C.steel }}> CONTROL PLANE</span>
        </button>
        {!firstVisit && <nav className="app-nav">
          {menu.items.map((l) => (
            <NavLink key={l.key} id={l.key} label={l.label} hint={l.hint}
              active={(PARENT_TAB[key] ?? key) === l.key} />
          ))}
        </nav>}
      </div>
      <div className="app-header-right">
        {!firstVisit && (
          <>
            <LearnToggle />
            <span className="hide-narrow hide-medium"><VersionBadge /></span>
            <ConnectionBadge onConnect={key === "connect"} />
            <SettingsButton active={key === "settings"} />
          </>
        )}
        <AccountButton />
      </div>
    </div>
  );
  const body = (
    <>
      <GuestSession />
      <AuthReturn />
      {key === "home" && <Home />}
      {key === "chaos" && <ChaosTests />}
      {key === "evidence" && <Evidence agent={route.query.agent} />}
      {key === "catch" && <Experiment runId={runIdParam} autostart={route.query.auto === "1"} />}
      {key === "explain" && (runIdParam
        ? <ReplayTab runId={runIdParam} cursor={route.query.cursor} seq={route.query.seq} node={route.query.node} />
        : <ReplaySearch query={route.query} />)}
      {key === "prevent" && <Regression rules={route.query.rules} />}
      {key === "govern" && (
        <>
          {!sidebar && <SubNav items={GOVERN_VIEWS} active={governView} />}
          {head === "health" ? <ContextHealth journey={menu.journey} /> : head === "sentinel" ? <Sentinel />
            : head === "trees" ? <Trees treeRun={runIdParam} />
            : head === "pair" ? <Pair token={runIdParam} withToken={route.query.with} node={route.query.node} />
            : runIdParam === "run" && route.segments[2]
              ? <RunControl runId={decodeURIComponent(route.segments[2])} />
              : <ControlTab focusNode={runIdParam} />}
        </>
      )}
      {key === "policy" && <ConfigBuilder />}
      {key === "skills" && <Skills />}
      {key === "skill-evals" && <SkillEvals evalId={runIdParam ? decodeURIComponent(runIdParam) : undefined} />}
      {key === "connect" && <Onboarding />}
      {key === "pricing" && <Pricing />}
      {key === "settings" && <Settings />}
      {!KNOWN.includes(key) && (
        <div style={{ maxWidth: 640, margin: "0 auto", fontFamily: MONO, fontSize: 12.5, color: C.mut }}>
          No page at <span style={{ color: C.text }}>#/{route.segments.join("/")}</span>.{" "}
          <span style={{ color: C.steel, cursor: "pointer" }} onClick={() => navigate("home")}>Go home →</span>
        </div>
      )}
    </>
  );

  if (sidebar) {
    return (
      <div style={{ minHeight: "100vh", background: C.bg, color: C.text, fontFamily: "Inter, system-ui, sans-serif", display: "flex" }}>
        <Sidebar active={PARENT_TAB[key] ?? key} view={key === "govern" ? governView : ""} />
        <main style={{ flex: 1, minWidth: 0, paddingBottom: 28 }}>
          <TopBar
            section={SECTION_TITLE[PARENT_TAB[key] ?? key] ?? ""}
            view={key === "govern" && governView !== "govern"
              ? (GOVERN_VIEWS.find(([r]) => r === governView)?.[1] ?? "") : ""}
            connectPage={key === "connect"} settingsPage={key === "settings"} />
          <div style={{ padding: "0 24px" }}>{body}</div>
        </main>
        <Tour />
      </div>
    );
  }
  return (
    <div style={{ minHeight: "100vh", background: C.bg, color: C.text, fontFamily: "Inter, system-ui, sans-serif", padding: "28px 20px" }}>
      {header}
      {body}
      <Tour />
    </div>
  );
}

/** Context health: a coding team's is its setup (skills, CLAUDE.md) checked
 * in a sandbox; a production agent's is probe's drift on its live context.
 * A journey sees its own; no journey or both sees both. */
function ContextHealth({ journey }: { journey: Journey | null }) {
  if (journey === "coding") return <SetupHealth />;
  if (journey === "production") return <Health />;
  return (
    <>
      <SetupHealth />
      <div style={{ maxWidth: 860, margin: "40px auto 28px", borderTop: `1px solid ${C.line}`, paddingTop: 8,
        fontFamily: MONO, fontSize: 10.5, color: C.dim, letterSpacing: "0.1em" }}>PRODUCTION AGENTS</div>
      <Health />
    </>
  );
}

const GOVERN_VIEWS: [string, string][] = [
  ["govern", "agents"], ["trees", "agent trees"], ["pair", "pair sessions"],
  ["sentinel", "data across sessions"], ["health", "context health & repair"],
];

const WIDE = "(min-width: 1000px)";

const SECTION_TITLE: Record<string, string> = {
  ...Object.fromEntries(LOOP.map((l) => [l.key, l.label])),
  home: "Home", connect: "Connect", pricing: "Pricing", settings: "Settings",
};

/** Whether the window is wide enough for the sidebar. */
function useWide(): boolean {
  const [wide, setWide] = useState(() =>
    typeof window !== "undefined" && window.matchMedia(WIDE).matches);
  useEffect(() => {
    const mq = window.matchMedia(WIDE);
    const on = () => setWide(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return wide;
}

/** The navigation as a sidebar: sections, Agents' views under it, and the
 * session's controls at the bottom. */
function Sidebar({ active, view }: { active: string; view: string }) {
  const menu = useMenu();
  const incidentCount = useIncidentCount();
  const item = (on: boolean, indent: boolean): React.CSSProperties => ({
    display: "block", width: "100%", textAlign: "left", cursor: "pointer",
    background: on ? "rgba(127,168,204,0.10)" : "none", border: "none",
    borderLeft: `2px solid ${on ? C.steel : "transparent"}`,
    color: on ? C.text : indent ? C.dim : C.mut,
    fontFamily: MONO, fontSize: indent ? 11.5 : 13, fontWeight: on && !indent ? 700 : 400,
    padding: indent ? "5px 10px 5px 26px" : "8px 10px 8px 14px", borderRadius: "0 6px 6px 0",
  });
  return (
    <aside data-testid="sidebar" style={{
      width: 232, flexShrink: 0, borderRight: `1px solid ${C.line}`, position: "sticky", top: 0,
      height: "100vh", display: "flex", flexDirection: "column", padding: "24px 12px 16px 0",
      overflowY: "auto",
    }}>
      <button onClick={() => navigate("home")} style={{
        background: "none", border: "none", cursor: "pointer", textAlign: "left", padding: "0 0 22px 16px",
        fontFamily: MONO, fontSize: 14, fontWeight: 700, color: C.text,
      }}>
        AXOR<span style={{ color: C.steel }}> CONTROL PLANE</span>
      </button>
      <nav className="flex flex-col" style={{ gap: 2 }}>
        {menu.items.map((l) => (
          <div key={l.key}>
            <button title={l.hint} onClick={() => navigate(l.key)}
              aria-current={active === l.key ? "page" : undefined}
              style={item(active === l.key && (l.key !== "govern" || view === "govern"), false)}>
              {l.label}
              {l.key === "evidence" && incidentCount > 0 && (
                <span data-testid="incidents-count" aria-hidden="true" title="new, needing a look" style={{ marginLeft: 8, fontSize: 10.5, color: C.bg, background: C.amber,
                  borderRadius: 9, padding: "0 6px", fontWeight: 700 }}>{incidentCount}</span>
              )}
            </button>
            {l.key === "govern" && (
              <div className="flex flex-col" style={{ gap: 1, margin: "2px 0 6px" }}>
                {GOVERN_VIEWS.slice(1).filter(([route]) => menu.all || menu.journey !== "coding"
                  || route === "trees" || route === "health" || route === "pair").map(([route, label]) => (
                  <button key={route} onClick={() => navigate(route)}
                    aria-current={view === route ? "page" : undefined}
                    data-testid={`side-${route}`} style={item(view === route, true)}>
                    {label}
                  </button>
                ))}
              </div>
            )}
          </div>
        ))}
      </nav>
      <MenuScope />
      <div style={{ flex: 1 }} />
      <div style={{ padding: "12px 0 0 16px" }}>
        <VersionBadge />
      </div>
    </aside>
  );
}

/** The bar over the page beside the sidebar: where you are, and the
 * session's controls — the header's job, without its navigation. */
function TopBar({ section, view, connectPage, settingsPage }: {
  section: string; view: string; connectPage: boolean; settingsPage: boolean;
}) {
  return (
    <div data-testid="topbar" className="flex items-center" style={{
      gap: 12, padding: "14px 24px", borderBottom: `1px solid ${C.line}`, marginBottom: 24,
      position: "sticky", top: 0, background: C.bg, zIndex: 20,
    }}>
      <div style={{ fontFamily: MONO, fontSize: 13, color: C.text, fontWeight: 700, minWidth: 0 }}>
        {section}
        {view && <span style={{ color: C.dim, fontWeight: 400 }}>{"  ·  "}{view}</span>}
      </div>
      <div style={{ flex: 1 }} />
      <LearnToggle />
      <ConnectionBadge onConnect={connectPage} />
      <SettingsButton active={settingsPage} />
      <AccountButton />
    </div>
  );
}

/** Two views of one job, side by side (Govern: nodes | health; Connect:
 * agent | config). Each keeps its own route, so both stay deep-linkable. */
function SubNav({ items, active }: { items: [string, string][]; active: string }) {
  return (
    <div style={{ maxWidth: 1200, margin: "-20px auto 20px" }}>
      <Tabs items={items.map(([id, label]) => ({ id, label }))} active={active}
        onChange={(id) => navigate(id)} />
    </div>
  );
}

function SettingsButton({ active }: { active: boolean }) {
  return (
    <button
      onClick={() => navigate("settings")}
      aria-label="settings"
      title="settings"
      style={{
        display: "flex", alignItems: "center", background: "none", cursor: "pointer",
        border: `1px solid ${active ? C.steel : C.line}`, borderRadius: 20, padding: "5px 8px",
        color: active ? C.steel : C.mut,
      }}
    >
      <SettingsIcon size={13} />
    </button>
  );
}

function LearnToggle() {
  const learn = useApp((s) => s.learnMode);
  const setLearn = useApp((s) => s.setLearnMode);
  return (
    <Tooltip
      content={
        learn
          ? "Learn mode is on — coach notes explain each surface. Click to turn off."
          : "Turn on Learn mode: concise coach notes appear on each screen to explain what it does."
      }
      side="bottom"
    >
      <button
        onClick={() => setLearn(!learn)}
        aria-pressed={learn}
        aria-label="toggle learn mode"
        data-tour="learn"
        style={{
          display: "flex", alignItems: "center", gap: 5,
          background: learn ? "rgba(127,168,204,0.12)" : "none",
          border: `1px solid ${learn ? C.steel : C.line}`, borderRadius: 20,
          padding: "4px 10px", cursor: "pointer",
          color: learn ? C.steel : C.mut, fontFamily: MONO, fontSize: 10.5,
        }}
      >
        <GraduationCap size={12} /> <span className="hide-narrow">learn</span>
      </button>
    </Tooltip>
  );
}

// The deployed version, where an operator can see it without going looking.
// Quiet by design: it renders nothing until the backend answers, and nothing at
// all if the call fails — a header that shouts "unknown" on every blip would be
// noise about the one thing that is almost never wrong. The kernel is the one
// shown, because that is the version deciding what this deployment DOES; the
// rest of the stack is in the tooltip.
function VersionBadge() {
  const [versions, setVersions] = useState<Record<string, string | null> | null>(null);
  useEffect(() => {
    let alive = true;
    api
      .version()
      .then((v) => {
        if (alive) setVersions(v.versions);
      })
      .catch(() => {
        /* not signed in yet, or a backend older than this route: say nothing */
      });
    return () => {
      alive = false;
    };
  }, []);
  const kernel = versions?.["axor-core"];
  if (!kernel) return null;
  const detail = Object.entries(versions ?? {})
    .filter(([, v]) => v)
    .map(([name, v]) => `${name} ${v}`)
    .join("\n");
  return (
    <span
      title={detail}
      style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, border: `1px solid ${C.line}`, borderRadius: 20, padding: "4px 10px" }}
    >
      kernel {kernel}
    </span>
  );
}

function ConnectionBadge({ onConnect }: { onConnect: boolean }) {
  const { mode } = useApp((s) => s.connection);
  // the coding example runs on the demo connection, but "mock tools" is the
  // production proxy's demo — for a coding visitor it is example data
  const nodes = useQuery({ queryKey: ["nodes"], queryFn: api.nodes, enabled: mode === "demo" });
  const ids = (nodes.data ?? []).map((n) => n.node_id);
  const label = mode === "demo" && (codingExampleOnly(ids) || fleetExampleOnly(ids))
    ? "example data" : MODE_LABEL[mode];
  // On the connect page itself a "connect" button leads where you already are.
  if (mode === "none" && onConnect) return null;
  if (mode === "none") {
    // the first step, so it is a button that says what it does — not a grey
    // status pill that happens to be clickable
    return (
      <button
        onClick={() => navigate("connect")}
        style={{
          display: "flex", alignItems: "center", gap: 6, background: C.steel, cursor: "pointer",
          border: `1px solid ${C.steel}`, borderRadius: 20, padding: "4px 12px",
          color: C.bg, fontFamily: MONO, fontSize: 10.5, fontWeight: 700, whiteSpace: "nowrap",
        }}
      >
        <Plug size={11} /> connect
      </button>
    );
  }
  const color = mode === "adapter" ? C.green : C.steel;
  return (
    <button
      onClick={() => navigate("connect")}
      title={`connection: ${label}`}
      aria-label={`connection: ${label}`}
      style={{ display: "flex", alignItems: "center", gap: 6, background: "none", border: `1px solid ${C.line}`, borderRadius: 20, padding: "6px 10px", cursor: "pointer" }}
    >
      <Circle size={7} fill={color} color={color} />
      {/* on a phone the dot alone: the full mode is one tap away, on Connect */}
      <span className="hide-narrow" style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, whiteSpace: "nowrap" }}>{label}</span>
    </button>
  );
}

/** Sign in from anywhere, in one click — not a wall in front of the product
 * and not a form buried in Settings. Anyone can look around first; the moment
 * they want their own data, the button is in the corner of every screen. */
function AccountButton() {
  const email = useApp((s) => s.identityEmail);
  const guest = isGuestToken(useApp((s) => s.apiToken));
  const guestExpiresAt = useApp((s) => s.guestExpiresAt);
  // re-render the countdown
  const [, tick] = useState(0);
  useEffect(() => {
    if (!guest) return;
    const t = window.setInterval(() => tick((n) => n + 1), 30_000);
    return () => window.clearInterval(t);
  }, [guest]);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  // A local `docker compose up` runs with auth OFF: every request already
  // works, and a "sign in" there is a button that changes nothing. Asked once;
  // a failed probe keeps the button, since offering a login is the safe side.
  const [authOn, setAuthOn] = useState<boolean | null>(null);
  useEffect(() => {
    let alive = true;
    api
      .authStatus()
      .then((st) => alive && setAuthOn(st.auth_enabled))
      .catch(() => alive && setAuthOn(true));
    return () => {
      alive = false;
    };
  }, []);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);
  if (authOn !== true && !email) return null;
  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button
        onClick={() => {
          if (!open && guest) track("signup_prompt_shown", "header");
          setOpen((v) => !v);
        }}
        aria-expanded={open}
        style={{
          background: "none", cursor: "pointer", borderRadius: 20, padding: "4px 12px",
          fontFamily: MONO, fontSize: 10.5,
          border: `1px solid ${email ? C.line : guest ? C.amber : C.steel}`,
          color: email ? C.mut : guest ? C.amber : C.steel,
          maxWidth: guest ? 260 : 180, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}
      >
        {email || (guest ? `save your work · guest, ${guestTimeLeft(guestExpiresAt)}` : "sign in")}
      </button>
      {open && (
        <div
          role="dialog"
          aria-label={email ? "account" : "sign in"}
          style={{
            position: "absolute", right: 0, top: "calc(100% + 8px)", zIndex: 50, width: 300,
            background: C.bg, border: `1px solid ${C.line}`, borderRadius: 8, padding: 14,
            boxShadow: "0 8px 24px rgba(0,0,0,0.35)",
          }}
        >
          {guest && (
            <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, lineHeight: 1.6, marginBottom: 10 }}>
              You are working as a guest: nothing is kept after the session ends. Sign up free —
              your runs, agents and incidents move into the new workspace.
            </div>
          )}
          <IdentityLogin initialMode={guest ? "signup" : "login"} onDone={() => setOpen(false)} />
        </div>
      )}
    </div>
  );
}
