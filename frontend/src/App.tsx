// The application shell. Router-driven, funnel-aware (spec section 3): the nav
// reflects where the user is on the connection ladder, and every surface is
// reachable by a deep link so EvidenceCases, replay moments and share permalinks
// are addressable. Quiet-until-wrong holds: three primary tabs, everything else
// behind "more", and adapter-only surfaces greyed with the honest upsell.
import { useEffect } from "react";
import { Circle, GraduationCap, Lock, Settings as SettingsIcon } from "lucide-react";
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
import Settings from "./tabs/Settings";
import Pricing from "./tabs/Pricing";

// Primary is the loop a first-time user actually runs: catch (eval) → explain
// (replay) → prevent (regression). Control is the fourth, adapter-only rung —
// shown so the destination is visible, greyed with an honest upsell until an
// adapter connection unlocks it. Everything else lives behind "more".
const PRIMARY = ["eval", "replay", "regression"] as const;
const MORE = ["get started", "config builder", "health", "expert", "pricing", "settings"] as const;

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
      <div className="flex items-center justify-between mb-8" style={{ maxWidth: 640, margin: "0 auto 36px" }}>
        <div className="flex items-center gap-6">
          <button
            onClick={() => navigate("home")}
            style={{ background: "none", border: "none", cursor: "pointer", fontFamily: MONO, fontSize: 14, fontWeight: 700, color: C.text, padding: 0 }}
          >
            AXOR<span style={{ color: C.steel }}> CONTROL PLANE</span>
          </button>
          <div className="flex gap-4 items-center">
            {PRIMARY.map((id) => (
              <NavLink key={id} id={id} active={key === id} />
            ))}
            <ControlNavLink active={key === "control"} mode={mode} />
            <MoreMenu activeKey={key} />
          </div>
        </div>
        <div className="flex items-center gap-3">
          <LearnToggle />
          <ConnectionBadge />
        </div>
      </div>

      {key === "home" && <Home />}
      {key === "eval" && <Experiment runId={runIdParam} autostart={route.query.auto === "1"} />}
      {key === "control" && <ControlTab focusNode={runIdParam} />}
      {key === "replay" && <ReplayTab runId={runIdParam} cursor={route.query.cursor} />}
      {key === "get started" && <Onboarding />}
      {key === "config builder" && <ConfigBuilder />}
      {key === "health" && <Health />}
      {key === "regression" && <Regression />}
      {key === "expert" && <ExpertView />}
      {key === "pricing" && <Pricing />}
      {key === "settings" && <Settings />}
      <Tour />
    </div>
  );
}

function MoreMenu({ activeKey }: { activeKey: string }) {
  const inMore = (MORE as readonly string[]).includes(activeKey);
  return (
    <div style={{ position: "relative" }} className="group">
      <details>
        <summary
          style={{
            listStyle: "none", cursor: "pointer", color: inMore ? C.text : C.dim,
            fontSize: 13, fontFamily: MONO,
          }}
        >
          more…
        </summary>
        <div style={{ position: "absolute", top: 24, left: 0, background: C.panel, border: `1px solid ${C.line}`, borderRadius: 6, zIndex: 10, minWidth: 150 }}>
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
        <GraduationCap size={12} /> learn
      </button>
    </Tooltip>
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
      <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut }}>{MODE_LABEL[mode]}</span>
    </button>
  );
}
