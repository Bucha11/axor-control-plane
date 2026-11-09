// The application shell. Router-driven, funnel-aware (spec section 3): the nav
// reflects where the user is on the connection ladder, and every surface is
// reachable by a deep link so EvidenceCases, replay moments and share permalinks
// are addressable. Quiet-until-wrong holds: three primary tabs, everything else
// behind "more", and adapter-only surfaces greyed with the honest upsell.
import { useEffect, useRef, useState } from "react";
import { Circle, GraduationCap, Lock, Settings as SettingsIcon } from "lucide-react";
import { api } from "./api";
import { C, MONO } from "./theme";
import { navigate, useRoute } from "./router";
import { ConnectionMode, MODE_LABEL, isAdapter, isConnected, useApp } from "./store";
import Tooltip from "./components/Tooltip";
import Tour from "./components/Tour";
import Home from "./tabs/Home";
import Experiment from "./tabs/Experiment";
import ControlTab from "./tabs/ControlTab";
import ReplayTab from "./tabs/ReplayTab";
import Onboarding from "./tabs/Onboarding";
import ConfigBuilder from "./tabs/ConfigBuilder";
import Health from "./tabs/Health";
import Regression from "./tabs/Regression";
import ExpertView from "./tabs/ExpertView";
import Settings, { IdentityLogin } from "./tabs/Settings";
import Pricing from "./tabs/Pricing";

// Primary is the loop a first-time user actually runs: catch (eval) → explain
// (replay) → prevent (regression). Control is the fourth, adapter-only rung —
// shown so the destination is visible, greyed with an honest upsell until an
// adapter connection unlocks it. Everything else lives behind "more".
const PRIMARY = ["eval", "replay", "regression"] as const;
const MORE = ["get started", "config builder", "health", "expert", "pricing", "settings"] as const;
const KNOWN = ["home", "control", ...PRIMARY, ...MORE] as const;

function NavLink({ id, active }: { id: string; active: boolean }) {
  return (
    <button
      onClick={() => navigate(id === "eval" ? "eval" : id.replace(/ /g, "-"))}
      style={{
        background: "none", border: "none", padding: "2px 0", cursor: "pointer",
        color: active ? C.text : C.dim, fontSize: 13, fontFamily: MONO,
        borderBottom: `2px solid ${active ? C.steel : "transparent"}`,
      }}
    >
      {id}
    </button>
  );
}

// Control is adapter-only by construction (the proxy has no handle on internal
// topology). Kept in the primary row so the destination reads as "next rung",
// but greyed with a lock + tooltip until adapter; clicking still lands on the
// tab's own upsell rather than pretending it's ready.
function ControlNavLink({ active, mode }: { active: boolean; mode: ConnectionMode }) {
  const unlocked = isAdapter(mode);
  const link = (
    <button
      onClick={() => navigate("control")}
      style={{
        display: "flex", alignItems: "center", gap: 4,
        background: "none", border: "none", padding: "2px 0", cursor: "pointer",
        color: active ? C.text : unlocked ? C.dim : C.line,
        fontSize: 13, fontFamily: MONO,
        borderBottom: `2px solid ${active ? C.steel : "transparent"}`,
      }}
    >
      {!unlocked && <Lock size={11} />}
      control
    </button>
  );
  if (unlocked) return link;
  return (
    <Tooltip content="Live topology + per-node interventions — unlocks at adapter depth (wrap your agent as an axor-core Invokable)." side="bottom">
      {link}
    </Tooltip>
  );
}

function routeKey(segments: string[]): string {
  const head = segments[0] ?? "home";
  if (head === "get-started") return "get started";
  if (head === "config-builder") return "config builder";
  return head;
}

export default function App() {
  const route = useRoute();
  const { mode } = useApp((s) => s.connection);
  const key = routeKey(route.segments);

  // First visit with no connection lands on Home (the funnel entry).
  useEffect(() => {
    if (route.segments.length === 0 && !isConnected(mode)) navigate("home");
  }, [route.segments.length, mode]);

  const runIdParam = route.segments[1];

  return (
    <div style={{ minHeight: "100vh", background: C.bg, color: C.text, fontFamily: "Inter, system-ui, sans-serif", padding: "28px 20px" }}>
      <div className="app-header" style={{ maxWidth: 920, margin: "0 auto 36px" }}>
        <div className="app-header-left">
          <button
            className="app-logo"
            onClick={() => navigate("home")}
            style={{ background: "none", border: "none", cursor: "pointer", fontFamily: MONO, fontSize: 14, fontWeight: 700, color: C.text, padding: 0, whiteSpace: "nowrap" }}
          >
            AXOR<span className="hide-narrow" style={{ color: C.steel }}> CONTROL PLANE</span>
          </button>
          <nav className="app-nav">
            {PRIMARY.map((id) => (
              <NavLink key={id} id={id} active={key === id} />
            ))}
            <ControlNavLink active={key === "control"} mode={mode} />
            <MoreMenu activeKey={key} />
          </nav>
        </div>
        <div className="app-header-right">
          <LearnToggle />
          <span className="hide-narrow"><VersionBadge /></span>
          <ConnectionBadge />
          <AccountButton />
        </div>
      </div>

      {key === "home" && <Home />}
      {key === "eval" && <Experiment runId={runIdParam} autostart={route.query.auto === "1"} />}
      {key === "control" && <ControlTab focusNode={runIdParam} />}
      {key === "replay" && <ReplayTab runId={runIdParam} cursor={route.query.cursor} seq={route.query.seq} node={route.query.node} />}
      {key === "get started" && <Onboarding />}
      {key === "config builder" && <ConfigBuilder />}
      {key === "health" && <Health />}
      {key === "regression" && <Regression />}
      {key === "expert" && <ExpertView />}
      {key === "pricing" && <Pricing />}
      {key === "settings" && <Settings />}
      {!(KNOWN as readonly string[]).includes(key) && (
        <div style={{ maxWidth: 640, margin: "0 auto", fontFamily: MONO, fontSize: 12.5, color: C.mut }}>
          No page at <span style={{ color: C.text }}>#/{route.segments.join("/")}</span>.{" "}
          <span style={{ color: C.steel, cursor: "pointer" }} onClick={() => navigate("home")}>Go home →</span>
        </div>
      )}
      <Tour />
    </div>
  );
}

function MoreMenu({ activeKey }: { activeKey: string }) {
  const inMore = (MORE as readonly string[]).includes(activeKey);
  const ref = useRef<HTMLDetailsElement>(null);
  // A <details> only closes on its own summary; close it on any outside click.
  useEffect(() => {
    const onDown = (e: MouseEvent): void => {
      if (ref.current?.open && !ref.current.contains(e.target as Node)) {
        ref.current.removeAttribute("open");
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);
  return (
    <div style={{ position: "relative" }} className="group">
      <details ref={ref}>
        <summary
          style={{
            listStyle: "none", cursor: "pointer", color: inMore ? C.text : C.dim,
            fontSize: 13, fontFamily: MONO, whiteSpace: "nowrap",
          }}
        >
          more…
        </summary>
        <div className="more-menu" style={{ position: "absolute", top: 24, background: C.panel, border: `1px solid ${C.line}`, borderRadius: 6, zIndex: 10, minWidth: 150 }}>
          {MORE.map((id) => (
            <button
              key={id}
              onClick={() => {
                navigate(id.replace(/ /g, "-"));
                (document.activeElement as HTMLElement)?.blur();
                document.querySelectorAll("details[open]").forEach((d) => d.removeAttribute("open"));
              }}
              style={{ display: "flex", alignItems: "center", gap: 6, width: "100%", textAlign: "left", background: "none", border: "none", padding: "8px 12px", cursor: "pointer", color: activeKey === id ? C.text : C.mut, fontSize: 12, fontFamily: MONO }}
            >
              {id === "settings" && <SettingsIcon size={12} />}
              {id}
            </button>
          ))}
        </div>
      </details>
    </div>
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
  const color = mode === "adapter" ? C.green : mode === "none" ? C.dim : C.steel;
  return (
    <button
      onClick={() => navigate("get-started")}
      title="connection status"
      style={{ display: "flex", alignItems: "center", gap: 6, background: "none", border: `1px solid ${C.line}`, borderRadius: 20, padding: "4px 10px", cursor: "pointer" }}
    >
      <Circle size={7} fill={color} color={color} />
      <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, whiteSpace: "nowrap" }}>{MODE_LABEL[mode]}</span>
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
