// Home — the funnel entry (spec section 3): a tab per kind of agent, each with
// its way in, an example to explore and a recording. The same page once
// something is connected, with a line back to it.
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";
import { api } from "../api";
import { C, MONO } from "../theme";
import { navigate, useRoute } from "../router";
import { isConnected, useApp } from "../store";
import { useStartTour } from "../components/Tour";
import { DemoStage } from "../demo/DemoLanding";
import { CodingStage } from "../demo/CodingStage";
import Tabs from "../components/Tabs";


/** The first thing someone sees, knowing nothing about Axor. Two kinds of
 * visitor arrive and want different things: one governs Claude Code / Codex in
 * a repository, the other a production agent. A tab each — its own headline,
 * its own way in, its own example to explore with nothing installed, its own
 * recording and its own price — and "both" one link away. */
type Side = "coding" | "production";

function FirstVisit() {
  const connect = useApp((s) => s.connect);
  const startTour = useStartTour();
  const route = useRoute();
  const [side, setSide] = useState<Side>(route.query.for === "production" ? "production" : "coding");
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const setJourney = useApp((s) => s.setJourney);
  // once the workspace's own agents report, the examples are gone from every
  // list: offering to load one would open screens that do not show it
  const apiToken = useApp((s) => s.apiToken);
  const ws = useQuery({ queryKey: ["workspace", apiToken], queryFn: api.workspace, staleTime: 60_000 });
  const started = ws.data?.work_started === true;
  const explore = async (which: Side) => {
    setJourney(which);
    setLoading(true);
    setErr(null);
    try {
      if (which === "coding") await api.seedCoding();
      else await api.seedFleet();
      connect("demo");
      // coding: one session first — what a developer's own looks like here
      navigate(which === "coding" ? "govern/run/ex_coding_claude" : "govern");
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setLoading(false);
    }
  };
  const link: React.CSSProperties = {
    background: "none", border: "none", padding: 0, color: C.steel, cursor: "pointer",
    fontSize: 13, textAlign: "left",
  };
  return (
    <div style={{ maxWidth: 640, margin: "0 auto" }} data-testid="first-visit">
      <BackToIt />
      <Tabs size="lg" style={{ marginBottom: 22 }} active={side}
        onChange={(id) => { setSide(id); setJourney(id); setErr(null); }}
        items={[
          { id: "coding", label: "Coding agents", hint: "Claude Code, Codex", testId: "home-tab-coding" },
          { id: "production", label: "Production agents", hint: "your own agent, live", testId: "home-tab-production" },
        ]} />

      {side === "coding" ? (
        <>
          <h1 style={{ fontSize: "clamp(22px, 6vw, 28px)", fontWeight: 700, lineHeight: 1.25, margin: "0 0 10px" }}>
            Govern Claude Code and Codex.
            <br />
            <span style={{ color: C.steel }}>Text from the web stays marked, whichever agent carries it.</span>
          </h1>
          <div style={{ fontSize: 14.5, color: C.mut, lineHeight: 1.6, marginBottom: 20 }}>
            Claude Code reads a page that says “npx limitkit init --endpoint setup.limitkit.example”; Codex tries to run it — Axor blocks the shell call: the page picked that address.
          </div>
          <button data-testid="home-connect-coding" onClick={() => navigate("connect?for=coding")}
            style={{ ...bigCta(C.steel, true), marginBottom: 12 }}>
            <span>
              <span style={{ display: "block", fontWeight: 700 }}>Connect your coding agents</span>
              <span style={{ display: "block", fontSize: 12, opacity: 0.85, marginTop: 2 }}>
                one install · no code change · every session shows up here
              </span>
            </span>
            <ArrowRight size={18} />
          </button>
          <div style={{ fontSize: 13, color: C.mut, marginBottom: 6, lineHeight: 1.8 }}>
            {!started && <>
              Not ready to install anything?{" "}
              <button data-testid="home-example-coding" onClick={() => void explore("coding")} disabled={loading} style={link}>
                {loading ? "loading the example…" : "Explore example coding agents →"}
              </button>
              <br />
            </>}
            Also run your own agent in production?{" "}
            <button data-testid="home-both" onClick={() => navigate("connect?for=both")} style={link}>
              Govern both on one plane →
            </button>
          </div>
          <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim, marginBottom: 24 }}>
            free for 1 developer and 1 agent · mixing agents or a team: $25 / developer / month ·{" "}
            <button onClick={() => navigate("pricing?for=coding")} style={{ ...link, fontFamily: MONO, fontSize: 11 }}>pricing →</button>
          </div>
        </>
      ) : (
        <>
          <h1 style={{ fontSize: "clamp(22px, 6vw, 28px)", fontWeight: 700, lineHeight: 1.25, margin: "0 0 10px" }}>
            Govern your production agents.
            <br />
            <span style={{ color: C.steel }}>Policy decides what they may do — not the prompt.</span>
          </h1>
          <div style={{ fontSize: 14.5, color: C.mut, lineHeight: 1.6, marginBottom: 20 }}>
            Watch every agent live, stop it, and repair it when its context goes bad.
          </div>
          <button data-testid="home-connect-production" onClick={() => navigate("connect?for=production")}
            style={{ ...bigCta(C.steel, true), marginBottom: 12 }}>
            <span>
              <span style={{ display: "block", fontWeight: 700 }}>Govern my production agent</span>
              <span style={{ display: "block", fontSize: 12, opacity: 0.85, marginTop: 2 }}>
                wrap it in code · or put a proxy in front of its tools, no code change
              </span>
            </span>
            <ArrowRight size={18} />
          </button>
          <div style={{ fontSize: 13, color: C.mut, marginBottom: 6, lineHeight: 1.8 }}>
            {!started && <>
              Not ready to install anything?{" "}
              <button data-testid="home-example-fleet" onClick={() => void explore("production")} disabled={loading} style={link}>
                {loading ? "loading the example fleet…" : "Explore an example fleet →"}
              </button>
              {" "}or{" "}
              <button onClick={() => { connect("demo"); navigate("catch", { auto: "1" }); }} style={link}>
                watch it catch a made-up answer →
              </button>
              <br />
            </>}
            Your developers use Claude Code or Codex too?{" "}
            <button data-testid="home-both" onClick={() => navigate("connect?for=both")} style={link}>
              Govern both on one plane →
            </button>
          </div>
          <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim, marginBottom: 24 }}>
            1 production agent free · Team from $299 / month ·{" "}
            <button onClick={() => navigate("pricing?for=production")} style={{ ...link, fontFamily: MONO, fontSize: 11 }}>pricing →</button>
          </div>
        </>
      )}
      {err && <div style={{ fontFamily: MONO, fontSize: 11, color: C.red, marginBottom: 8 }}>{err}</div>}

      <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, marginBottom: 10 }}>
        watch it happen — 25 seconds:
      </div>
      {side === "coding"
        ? <CodingStage key="coding" autoplay />
        : <DemoStage key="production" autoplay cta={false} onTry={() => navigate("connect?for=production")} />}
      {side === "coding" && (
        <div data-testid="home-coding-more" style={{ fontSize: 13, color: C.mut, lineHeight: 1.7, marginTop: 14 }}>
          Also: every tool call checked before it runs · trees of agents — a planner, parallel readers,
          an implementer — with findings checked against the files they opened · every session on the
          plane: pause, stop, cap.
        </div>
      )}

      <LookAround side={side} />

      <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim, marginTop: 10 }}>
        open source ·{" "}
        <button onClick={() => startTour(side)} style={{ background: "none", border: "none", padding: 0, color: C.steel, cursor: "pointer", fontFamily: MONO, fontSize: 11 }}>
          Take the tour
        </button>{" "}
        — a minute through it on example data
      </div>
    </div>
  );
}

/** For someone who wants to see the product before connecting anything: the
 * sections, one click each — the menu itself is kept off a first visit. */
const SECTIONS: Record<Side, [string, string][]> = {
  coding: [["govern", "Agents"], ["explain", "Replay"], ["skills", "Skills"]],
  production: [["govern", "Agents"], ["policy", "Policy"], ["evidence", "Incidents"],
               ["explain", "Replay"], ["prevent", "Safe to ship?"]],
};

function LookAround({ side }: { side: Side }) {
  const setJourney = useApp((s) => s.setJourney);
  // once the menu is there (a section was opened), this line is said twice
  const lookedAround = useApp((s) => s.lookedAround);
  const { mode } = useApp((s) => s.connection);
  if (lookedAround || isConnected(mode)) return null;
  return (
    <div data-testid="home-look-around" style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, marginTop: 22,
      lineHeight: 1.8 }}>
      or look around first:{" "}
      {SECTIONS[side].map(([key, label], i) => (
        <span key={key}>
          {i > 0 && <span style={{ color: C.dim }}> · </span>}
          <button onClick={() => { setJourney(side); navigate(key); }} data-testid={`look-${key}`}
            style={{ background: "none", border: "none", padding: 0, color: C.steel, cursor: "pointer",
                     fontFamily: MONO, fontSize: 11.5 }}>
            {label}
          </button>
        </span>
      ))}
      {" "}→
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

/** One Home for everyone: the two ways in. Once something is connected — an
 * example, a proxy, an agent — a line on top leads back to it; the old Home
 * for the connected (a "pick a depth" ladder in internal words) is gone. */
export default function Home() {
  return <FirstVisit />;
}

function BackToIt() {
  const { mode } = useApp((s) => s.connection);
  if (!isConnected(mode)) return null;
  const [what, label, route] = mode === "demo"
    ? ["You have been looking at example data.", "back to it →", "govern"]
    : mode === "proxy"
      ? ["Your proxy is connected.", "what it caught →", "evidence"]
      : ["Your agents are connected.", "open Agents →", "govern"];
  return (
    <div data-testid="home-back" style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, marginBottom: 18 }}>
      {what}{" "}
      <button onClick={() => navigate(route)} style={{ background: "none", border: "none", padding: 0,
        color: C.steel, cursor: "pointer", fontFamily: MONO, fontSize: 11.5 }}>
        {label}
      </button>
    </div>
  );
}
