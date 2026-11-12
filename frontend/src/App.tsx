// The application shell. Router-driven, funnel-aware (spec section 3): every
// surface is reachable by a deep link so EvidenceCases, replay moments and share
// permalinks are addressable.
//
// The nav leads with governance, the product; the catch loop is its proof:
//   Agents         (#/govern)   — governed agents: graph, sentinel, health & repair
//   Policy         (#/policy)   — what each agent may read, send and run
//   Caught lies    (#/evidence) — every EvidenceCase caught (tests run at #/catch)
//   Replay         (#/explain)  — step through a run, fork what-ifs
//   Safe to ship?  (#/prevent)  — a policy change against the caught cases
// Older route names stay as aliases, so no shared link breaks.
// It used to read eval · replay · regression · control · more…, which named
// the machinery instead of the job, and hid the first step (get started) and a
// static mock (expert) behind the same "more".
import { useEffect, useRef, useState } from "react";
import { Circle, GraduationCap, Plug, Settings as SettingsIcon } from "lucide-react";
import { api } from "./api";
import { C, MONO } from "./theme";
import { navigate, useRoute } from "./router";
import { MODE_LABEL, isConnected, useApp } from "./store";
import Tooltip from "./components/Tooltip";
import Tour from "./components/Tour";
import Home from "./tabs/Home";
import Evidence from "./tabs/Evidence";
import Experiment from "./tabs/Experiment";
import ControlTab from "./tabs/ControlTab";
import ReplayTab from "./tabs/ReplayTab";
import Onboarding from "./tabs/Onboarding";
import RunControl from "./tabs/RunControl";
import ConfigBuilder from "./tabs/ConfigBuilder";
import Health from "./tabs/Health";
import Sentinel from "./tabs/Sentinel";
import Regression from "./tabs/Regression";
import Settings, { IdentityLogin } from "./tabs/Settings";
import Pricing from "./tabs/Pricing";

// Governance first: the product is the policy membrane between an agent and its
// tools. "Caught lies" is the proof it works, not the product.
const LOOP = [
  { key: "govern", label: "Agents", hint: "Your governed agents as a graph: pause, cap, stop — what sentinel sees across sessions, what probe says about their health." },
  { key: "policy", label: "Policy", hint: "What each agent may read, send and run — the rules enforced at the tool boundary." },
  { key: "evidence", label: "Caught lies", hint: "Every time an agent claimed something its tools never gave it — and the test that keeps it caught." },
  { key: "explain", label: "Replay", hint: "Step through any run and see exactly what crossed the boundary, and why." },
  { key: "prevent", label: "Safe to ship?", hint: "Check a policy change against every case you've caught, before it ships." },
] as const;

/** Screens without a tab of their own light up the tab they belong to. */
const PARENT_TAB: Record<string, string> = { catch: "evidence" };

/** Old route heads keep working — they are in shared links and bookmarks. */
const ALIAS: Record<string, string> = {
  eval: "catch",
  replay: "explain",
  regression: "prevent",
  control: "govern",
  health: "govern",
  sentinel: "govern",
  "get-started": "connect",
  "config-builder": "policy",
};
const KNOWN = ["home", "evidence", "catch", "explain", "prevent", "govern", "policy", "connect", "pricing", "settings"];

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
  // Someone who has not tried anything yet sees no menu on Home: five tabs of
  // product vocabulary is what made a first visit read as "close and leave".
  const firstVisit = key === "home" && !isConnected(mode);

  return (
    <div style={{ minHeight: "100vh", background: C.bg, color: C.text, fontFamily: "Inter, system-ui, sans-serif", padding: "28px 20px" }}>
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
            {LOOP.map((l) => (
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
              <ConnectionBadge />
              <SettingsButton active={key === "settings"} />
            </>
          )}
          <AccountButton />
        </div>
      </div>

      {key === "home" && <Home />}
      {key === "evidence" && <Evidence />}
      {key === "catch" && <Experiment runId={runIdParam} autostart={route.query.auto === "1"} />}
      {key === "explain" && <ReplayTab runId={runIdParam} cursor={route.query.cursor} seq={route.query.seq} node={route.query.node} />}
      {key === "prevent" && <Regression />}
      {key === "govern" && (
        <>
          <SubNav
            items={[["govern", "agents"], ["sentinel", "sentinel · across sessions"], ["health", "health & repair"]]}
            active={head === "health" || head === "sentinel" ? head : "govern"}
          />
          {head === "health" ? <Health /> : head === "sentinel" ? <Sentinel />
            : runIdParam === "run" && route.segments[2]
              ? <RunControl runId={decodeURIComponent(route.segments[2])} />
              : <ControlTab focusNode={runIdParam} />}
        </>
      )}
      {key === "policy" && <ConfigBuilder />}
      {key === "connect" && <Onboarding />}
      {key === "pricing" && <Pricing />}
      {key === "settings" && <Settings />}
      {!KNOWN.includes(key) && (
        <div style={{ maxWidth: 640, margin: "0 auto", fontFamily: MONO, fontSize: 12.5, color: C.mut }}>
          No page at <span style={{ color: C.text }}>#/{route.segments.join("/")}</span>.{" "}
          <span style={{ color: C.steel, cursor: "pointer" }} onClick={() => navigate("home")}>Go home →</span>
        </div>
      )}
      <Tour />
    </div>
  );
}

/** Two views of one job, side by side (Govern: nodes | health; Connect:
 * agent | config). Each keeps its own route, so both stay deep-linkable. */
function SubNav({ items, active }: { items: [string, string][]; active: string }) {
  return (
    <div className="flex gap-2 flex-wrap" style={{ maxWidth: 640, margin: "-20px auto 20px" }}>
      {items.map(([route, label]) => (
        <button
          key={route}
          onClick={() => navigate(route)}
          aria-pressed={active === route}
          style={{
            background: active === route ? "rgba(127,168,204,0.12)" : "none",
            border: `1px solid ${active === route ? C.steel : C.line}`, borderRadius: 20,
            padding: "4px 12px", cursor: "pointer", fontFamily: MONO, fontSize: 11,
            color: active === route ? C.steel : C.mut,
          }}
        >
          {label}
        </button>
      ))}
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

function ConnectionBadge() {
  const { mode } = useApp((s) => s.connection);
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
      title={`connection: ${MODE_LABEL[mode]}`}
      aria-label={`connection: ${MODE_LABEL[mode]}`}
      style={{ display: "flex", alignItems: "center", gap: 6, background: "none", border: `1px solid ${C.line}`, borderRadius: 20, padding: "6px 10px", cursor: "pointer" }}
    >
      <Circle size={7} fill={color} color={color} />
      {/* on a phone the dot alone: the full mode is one tap away, on Connect */}
      <span className="hide-narrow" style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, whiteSpace: "nowrap" }}>{MODE_LABEL[mode]}</span>
    </button>
  );
}

/** Sign in from anywhere, in one click — not a wall in front of the product
 * and not a form buried in Settings. Anyone can look around first; the moment
 * they want their own data, the button is in the corner of every screen. */
function AccountButton() {
  const email = useApp((s) => s.identityEmail);
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
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        style={{
          background: "none", cursor: "pointer", borderRadius: 20, padding: "4px 12px",
          fontFamily: MONO, fontSize: 10.5,
          border: `1px solid ${email ? C.line : C.steel}`,
          color: email ? C.mut : C.steel,
          maxWidth: 180, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}
      >
        {email || "sign in"}
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
          <IdentityLogin onDone={() => setOpen(false)} />
        </div>
      )}
    </div>
  );
}
