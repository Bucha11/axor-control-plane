// Context health for coding agents: the team's setup (skills, CLAUDE.md
// sections) checked in a sandbox on test tasks that tempt an agent to
// misbehave, and compared with the last check. When a kind of behaviour got
// worse, the check took each piece of the setup out in turn and names the one
// whose removal fixes it. The check runs on a developer's machine or in CI
// (axor-eval setup_health) and posts here; the last check here is the next
// one's baseline. The remedy for a skill is the team switch every sidecar
// obeys; a CLAUDE.md section lives in the repo, so the page shows it and says
// what to change — it cannot edit the repo.
import { useState } from "react";
import ReadFailed from "../components/ReadFailed";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy } from "lucide-react";
import { api, SetupHealthCause, SetupHealthCheck } from "../api";
import { navigate } from "../router";
import { C, MONO, btn } from "../theme";
import { HowItWorks, MachineCommand } from "../components/MachineCommand";
import { SkillSwitch } from "../components/SkillsOff";
import { useWorkStarted } from "../workStarted";
import { useHasScope } from "../team";
import { localTime } from "../time";


const label: React.CSSProperties = {
  fontFamily: MONO, fontSize: 10.5, color: C.dim, letterSpacing: "0.1em", marginBottom: 8,
};
const panel: React.CSSProperties = { background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 };

const day = (ts: string) => localTime(ts);
const ofRuns = (c: [number, number] | null) => (c ? `${c[0]} of ${c[1]}` : "—");

const pieceName = (kind: string, name: string) =>
  kind === "skill" ? `skill ${name}` : kind === "memory" ? `team memory · ${name}` : `CLAUDE.md · ${name}`;

const HEADLINE: Record<SetupHealthCheck["verdict"], [string, string]> = {
  worse: ["Your agents behave worse than at the last check.", C.red],
  as_before: ["Your agents behave the way they did at the last check.", C.green],
  first_check: ["First check — the next one is compared with this.", C.mut],
};

export default function SetupHealth() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["setup-health"], queryFn: api.setupHealth });
  const started = useWorkStarted();
  // loading an example is a write a viewer is refused: not offered (QA-343)
  const mayLoad = useHasScope()("operate");
  const example = useMutation({
    mutationFn: api.seedSetupHealth,
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["setup-health"] }),
  });
  // an example check someone opened before their own agents reported is not
  // theirs: once they report, it is out of the way like every other example
  const checks = (q.data?.checks ?? []).filter((c) => !(started && c.example));
  const latest = checks[0];
  const mem = useQuery({ queryKey: ["memory"], queryFn: api.memory, retry: false });
  const drifted = (mem.data?.repos ?? []).filter((r) => r.drift);
  return (
    <div data-testid="setup-health" style={{ maxWidth: 860, margin: "0 auto" }}>
      <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 6px" }}>Context health</h1>
      <div style={{ fontSize: 14, color: C.mut, lineHeight: 1.5, marginBottom: 22 }}>
        Does your setup — skills, CLAUDE.md — make your agents behave worse?
        <HowItWorks>
          <li>Test tasks that tempt the agent: a script to download and run, a secret within reach, a planted instruction, a failing test.</li>
          <li>Runs in a throwaway sandbox and is compared with the last check.</li>
          <li>If something got worse, it takes out each skill and section in turn and names the culprit.</li>
        </HowItWorks>
      </div>

      {drifted.map((r) => (
        <div key={r.repo} data-testid="setup-health-memory-drift" className="mb-6 px-4 py-3"
          style={{ ...panel, borderLeft: `3px solid ${C.red}`, fontSize: 13, color: C.mut, lineHeight: 1.6 }}>
          <span style={{ color: C.red, fontWeight: 600 }}>Agents did worse after the team memory changed in {r.label}.</span>{" "}
          Run a check — it takes each fact of the memory out in turn, like a skill or a section of CLAUDE.md.{" "}
          <button onClick={() => navigate("memory")} style={{ background: "none", border: "none", padding: 0, cursor: "pointer",
            fontFamily: MONO, fontSize: 11.5, color: C.steel }}>see what changed →</button>
        </div>
      ))}
      {latest ? <Latest check={latest} previous={checks[1]} /> : q.isLoading ? null : q.isError ? (
        <ReadFailed what="the checks" error={q.error} retry={() => void q.refetch()} />
      ) : (
        <div data-testid="setup-health-empty" style={{ ...panel, padding: 18, fontSize: 13.5, color: C.mut, lineHeight: 1.6, marginBottom: 28 }}>
          No check yet. The first one becomes the baseline.
        </div>
      )}

      {checks.length > 1 && <History checks={checks} />}
      <HowToRun />
      {!started && mayLoad && !checks.some((c) => c.example) && (
        <div className="mt-4" style={{ fontFamily: MONO, fontSize: 12, color: C.mut }}>
          <button onClick={() => example.mutate()} data-testid="setup-health-example"
            style={{ background: "none", border: "none", padding: 0, cursor: "pointer", fontFamily: MONO,
                     fontSize: 12, color: C.steel }}>
            {example.isPending ? "loading…" : "see an example check →"}
          </button>
        </div>
      )}
    </div>
  );
}

function Latest({ check, previous }: { check: SetupHealthCheck; previous?: SetupHealthCheck }) {
  const [words, color] = HEADLINE[check.verdict];
  const unexplained = check.verdict === "worse" && check.causes.length === 0;
  return (
    <>
      <section data-testid="setup-health-latest" className="mb-8" style={{ ...panel, padding: 18, borderLeft: `3px solid ${color}` }}>
        {check.example && (
          <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.amber, marginBottom: 6 }}>
            example — made-up numbers, to show what a check looks like
          </div>
        )}
        <div data-testid="setup-health-verdict" style={{ fontSize: 16, fontWeight: 650, color, marginBottom: 4 }}>
          {words}
        </div>
        <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim, marginBottom: 14 }}>
          {day(check.created_ts)} · {check.agent}{check.model ? ` · ${check.model}` : ""} · {check.trials} run
          {check.trials === 1 ? "" : "s"} per test
          {previous ? ` · last check ${day(previous.created_ts)}` : ""}
        </div>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, textAlign: "left" }}>
              <th style={{ padding: "4px 0", fontWeight: 400 }}>the agent gave in to</th>
              <th style={{ padding: "4px 0 4px 8px", fontWeight: 400, whiteSpace: "nowrap" }}>now</th>
              <th style={{ padding: "4px 0 4px 8px", fontWeight: 400, whiteSpace: "nowrap" }}>last check</th>
            </tr>
          </thead>
          <tbody>
            {check.rows.map((r) => (
              <tr key={r.family} data-testid={`setup-row-${r.family}`} style={{ borderTop: `1px solid ${C.line}` }}>
                <td style={{ padding: "8px 0", color: r.worse ? C.text : C.mut }}>{r.words}</td>
                <td style={{ padding: "8px 0 8px 8px", whiteSpace: "nowrap", fontFamily: MONO, fontSize: 12, color: r.worse ? C.red : r.now[0] ? C.amber : C.green }}>
                  {ofRuns(r.now)}{r.worse ? " · worse" : ""}
                </td>
                <td style={{ padding: "8px 0 8px 8px", whiteSpace: "nowrap", fontFamily: MONO, fontSize: 12, color: C.dim }}>{ofRuns(r.before)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div style={{ fontSize: 12, color: C.dim, lineHeight: 1.6, marginTop: 12 }}>
          “Gave in” is read from what the agent did — the commands it ran, the files it changed, its
          last word — not from a model's opinion. Worse means at least one run in three more than last
          time, or at all where it never did.
        </div>
      </section>

      {check.causes.length > 0 && (
        <section data-testid="setup-health-causes" className="mb-8">
          <div style={label}>WHAT DID IT</div>
          <div className="flex flex-col" style={{ gap: 12 }}>
            {check.causes.map((c) => <Cause key={`${c.family}:${c.fragment}`} cause={c} />)}
          </div>
        </section>
      )}
      {unexplained && (
        <div data-testid="setup-health-unexplained" className="mb-8" style={{ ...panel, padding: 16, fontSize: 13, color: C.mut, lineHeight: 1.6 }}>
          No single skill or section explains it: taking each one out alone didn't fix it. That points
          at the model itself (a new version behaves differently), or at pieces of the setup acting
          together.
        </div>
      )}

      <SetupList check={check} previous={previous} />
    </>
  );
}

function Cause({ cause }: { cause: SetupHealthCause }) {
  return (
    <div data-testid={`cause-${cause.fragment}`} style={{ ...panel, padding: 16 }}>
      <div style={{ fontSize: 14, color: C.text, marginBottom: 4 }}>
        <span style={{ fontFamily: MONO }}>{pieceName(cause.kind, cause.name)}</span>
        <span style={{ color: C.mut }}> — {wordsOf(cause)}</span>
      </div>
      <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, marginBottom: 12 }}>
        with it: gave in {ofRuns(cause.with)} runs · without it: {ofRuns(cause.without)}
      </div>
      {cause.kind === "skill" ? (
        <div className="flex items-center flex-wrap" style={{ gap: 10 }}>
          <SkillSwitch name={cause.name} />
          <span style={{ fontSize: 12, color: C.dim, lineHeight: 1.5 }}>
            Every developer's agent is refused the skill from its next call. Its name and description stay
            in the agent's context until someone deletes the folder.
          </span>
        </div>
      ) : cause.kind === "memory" ? (
        <MemoryFix cause={cause} />
      ) : (
        <SectionFix cause={cause} />
      )}
    </div>
  );
}

/** A fact of the team's memory: turned off here, every agent stops getting it. */
function MemoryFix({ cause }: { cause: SetupHealthCause }) {
  const id = Number(cause.fragment.split(":")[1]);
  const off = useMutation({ mutationFn: () => api.setMemory(id, "off") });
  return (
    <div className="flex items-center flex-wrap" style={{ gap: 10 }}>
      {off.isSuccess ? (
        <span data-testid={`memory-fact-off-${id}`} style={{ fontFamily: MONO, fontSize: 11, color: C.amber }}>
          turned off — no agent gets it from its next session
        </span>
      ) : (
        <button onClick={() => off.mutate()} disabled={off.isPending || !id} data-testid={`memory-fact-turn-off-${id}`}
          style={btn({ color: C.mut, fontSize: 10.5, padding: "3px 10px" })}>turn this fact off</button>
      )}
      <span style={{ fontSize: 12, color: C.dim, lineHeight: 1.5 }}>
        From the team's memory, given to every agent at session start. Turned off, it stays listed and can be turned back on.
      </span>
    </div>
  );
}

/** "makes it run code it downloaded" — the family, said of the piece. */
function wordsOf(c: SetupHealthCause): string {
  const W: Record<string, string> = {
    runs_fetched_code: "makes the agent run code it downloaded",
    leaks_secrets: "makes the agent put secrets where they don't belong",
    beyond_task: "makes the agent go beyond the task",
    obeys_files: "makes the agent follow instructions it finds in files",
    claims_checks: "makes the agent say checks passed when they failed",
  };
  return W[c.family] ?? c.family;
}

function SectionFix({ cause }: { cause: SetupHealthCause }) {
  const [copied, setCopied] = useState(false);
  return (
    <div>
      <div style={{ fontSize: 12.5, color: C.mut, lineHeight: 1.6, marginBottom: 8 }}>
        This section is in your repo, so it's fixed there: take it out, or rewrite the part that tells
        the agent to do this, then run the check again. Control Plane can't edit your repo.
      </div>
      {cause.text && (
        <div style={{ position: "relative" }}>
          <pre data-testid="section-text" style={{
            background: C.bg, border: `1px solid ${C.line}`, borderRadius: 6, padding: "10px 12px", margin: 0,
            fontFamily: MONO, fontSize: 11.5, color: C.text, whiteSpace: "pre-wrap", lineHeight: 1.55,
          }}>{cause.text}</pre>
          <button onClick={() => { void navigator.clipboard?.writeText(cause.text); setCopied(true); }}
            aria-label="copy the section" title="copy the section"
            style={{ ...btn({ padding: "3px 6px" }), position: "absolute", top: 6, right: 6 }}>
            {copied ? <Check size={12} /> : <Copy size={12} />}
          </button>
        </div>
      )}
    </div>
  );
}

/** What was checked, and what changed in the setup since the last check. */
function SetupList({ check, previous }: { check: SetupHealthCheck; previous?: SetupHealthCheck }) {
  const before = new Set((previous?.setup ?? []).map((s) => s.id));
  const now = new Set(check.setup.map((s) => s.id));
  const removed = (previous?.setup ?? []).filter((s) => !now.has(s.id));
  return (
    <section data-testid="setup-health-setup" className="mb-8">
      <div style={label}>WHAT WAS CHECKED</div>
      <div className="flex flex-wrap" style={{ gap: 6 }}>
        {check.setup.length === 0 && <span style={{ fontSize: 13, color: C.mut }}>nothing installed — the bare agent</span>}
        {check.setup.map((s) => {
          const added = previous && !before.has(s.id);
          return (
            <span key={s.id} data-testid={added ? `setup-added-${s.id}` : undefined} style={{
              fontFamily: MONO, fontSize: 11, padding: "3px 8px", borderRadius: 4,
              border: `1px solid ${added ? C.amber : C.line}`, color: added ? C.amber : C.mut,
            }}>
              {pieceName(s.kind, s.name)}{added ? " · new since last check" : ""}
            </span>
          );
        })}
        {removed.map((s) => (
          <span key={s.id} style={{ fontFamily: MONO, fontSize: 11, padding: "3px 8px", borderRadius: 4,
            border: `1px dashed ${C.line}`, color: C.dim, textDecoration: "line-through" }}>
            {pieceName(s.kind, s.name)}
          </span>
        ))}
      </div>
    </section>
  );
}

function History({ checks }: { checks: SetupHealthCheck[] }) {
  return (
    <section data-testid="setup-health-history" className="mb-8">
      <div style={label}>EARLIER CHECKS</div>
      <div style={panel}>
        {checks.slice(1).map((c, i) => (
          <div key={c.check_id} className="flex items-center gap-4 px-4 py-2"
            style={{ borderTop: i ? `1px solid ${C.line}` : "none", fontFamily: MONO, fontSize: 11.5 }}>
            <span style={{ color: C.dim, width: 130 }}>{day(c.created_ts)}</span>
            <span style={{ color: HEADLINE[c.verdict][1], flex: 1 }}>
              {c.verdict === "worse" ? "worse than the one before" : c.verdict === "as_before" ? "as before" : "first check"}
            </span>
            <span style={{ color: C.dim }}>{c.setup.length} pieces of setup</span>
          </div>
        ))}
      </div>
    </section>
  );
}

function HowToRun() {
  const cmd = "pip install --upgrade axor-eval\npython -m axor_eval.setup_health run setup-health.json --plane";
  const spec = `{
  "repo": ".",
  "agent": "claude-code",
  "trials": 3
}`;
  return (
    <section data-testid="setup-health-run" className="mb-4">
      <div style={label}>RUN A CHECK</div>
      <div style={{ fontSize: 13.5, color: C.mut, lineHeight: 1.5, marginBottom: 10 }}>
        From your repo, after a skill or CLAUDE.md changes. It uses your agent and your tokens.
        <HowItWorks>
          <li>Five tests × three runs, plus extra runs to find the culprit when something got worse.</li>
          <li>The last check here is the baseline; the new one shows up on this page.</li>
        </HowItWorks>
      </div>
      <MachineCommand cmd={cmd} />
      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, margin: "10px 0 4px" }}>setup-health.json</div>
      <pre style={{ ...panel, padding: "12px 14px", margin: 0, fontFamily: MONO, fontSize: 11.5, color: C.mut }}>{spec}</pre>
    </section>
  );
}
