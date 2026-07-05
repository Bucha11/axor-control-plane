// Shell: quiet-until-wrong. Three primary tabs; setup surfaces live under
// "more" — density is earned by problems, not by navigation.
import { useState } from "react";
import { C, MONO } from "./theme";
import EvalTab from "./tabs/EvalTab";
import ControlTab from "./tabs/ControlTab";
import ReplayTab from "./tabs/ReplayTab";
import Onboarding from "./tabs/Onboarding";
import ConfigBuilder from "./tabs/ConfigBuilder";
import Health from "./tabs/Health";
import Regression from "./tabs/Regression";
import ExpertView from "./tabs/ExpertView";

const PRIMARY = ["eval", "control", "replay"] as const;
const MORE = ["get started", "config builder", "health", "regression", "expert"] as const;
type Tab = (typeof PRIMARY)[number] | (typeof MORE)[number];

export default function App() {
  const [tab, setTab] = useState<Tab>("eval");
  const [moreOpen, setMoreOpen] = useState(false);

  return (
    <div style={{ minHeight: "100vh", background: C.bg, color: C.text, fontFamily: "Inter, system-ui, sans-serif", padding: "28px 20px" }}>
      <div className="flex items-center gap-6" style={{ maxWidth: 640, margin: "0 auto 36px" }}>
        <span style={{ fontFamily: MONO, fontSize: 14, fontWeight: 700 }}>
          AXOR<span style={{ color: C.steel }}> CONTROL PLANE</span>
        </span>
        <div className="flex gap-4 items-center">
          {PRIMARY.map((id) => (
            <button key={id} onClick={() => setTab(id)}
              style={{ background: "none", border: "none", padding: "2px 0", cursor: "pointer",
                color: tab === id ? C.text : C.dim, fontSize: 13, fontFamily: MONO,
                borderBottom: `2px solid ${tab === id ? C.steel : "transparent"}` }}>
              {id}
            </button>
          ))}
          <div style={{ position: "relative" }}>
            <button onClick={() => setMoreOpen(!moreOpen)}
              style={{ background: "none", border: "none", cursor: "pointer",
                color: (MORE as readonly string[]).includes(tab) ? C.text : C.dim,
                fontSize: 13, fontFamily: MONO }}>
              more…
            </button>
            {moreOpen && (
              <div style={{ position: "absolute", top: 24, left: 0, background: C.panel,
                border: `1px solid ${C.line}`, borderRadius: 6, zIndex: 10, minWidth: 140 }}>
                {MORE.map((id) => (
                  <button key={id} onClick={() => { setTab(id); setMoreOpen(false); }}
                    style={{ display: "block", width: "100%", textAlign: "left", background: "none",
                      border: "none", padding: "8px 12px", cursor: "pointer",
                      color: tab === id ? C.text : C.mut, fontSize: 12, fontFamily: MONO }}>
                    {id}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
      {tab === "eval" && <EvalTab />}
      {tab === "control" && <ControlTab />}
      {tab === "replay" && <ReplayTab />}
      {tab === "get started" && <Onboarding />}
      {tab === "config builder" && <ConfigBuilder />}
      {tab === "health" && <Health />}
      {tab === "regression" && <Regression />}
      {tab === "expert" && <ExpertView />}
    </div>
  );
}
