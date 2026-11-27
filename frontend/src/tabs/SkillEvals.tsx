// Skill evals — which of your coding agent's skills help, and what they cost.
//
// The eval itself runs on the developer's machine (axor-eval: the same tasks
// with no skill, all, each alone and all but one; solved = the task's tests
// passed), and `--plane` posts its report here. This page reads that report as
// written: per skill, what it does alone and on top of the others, whether the
// agent opened it at all, and its token cost; per arm, solved and spend.
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Loader2 } from "lucide-react";
import { api, SkillEval, SkillEvalArm, SkillEvalCompare, SkillEvalSkill, SkillEvalTask } from "../api";
import { navigate } from "../router";
import { isGuestToken, useApp } from "../store";
import { C, MONO, btn } from "../theme";
import { SkillSwitch, TeamSwitch } from "../components/SkillsOff";
import { HowItWorks, MachineCommand } from "../components/MachineCommand";
import { useWorkStarted } from "../workStarted";
import { localTime } from "../time";
import { useOneAtATime } from "../oneAtATime";

const label: React.CSSProperties = {
  fontFamily: MONO, fontSize: 10.5, color: C.dim, letterSpacing: "0.1em", marginBottom: 8,
};

export function verdictColor(v: string): string {
  if (v === "helps" || v.startsWith("same results")) return C.green;
  if (v === "hurts") return C.red;
  if (v.startsWith("costs tokens") || v.startsWith("never opened")) return C.amber;
  return C.mut;
}

const n = (x: number | null | undefined) => (x == null ? "—" : Math.round(x).toLocaleString("en-US"));
const usd = (x: number | null | undefined) => (x == null ? "—" : `$${x.toFixed(3)}`);
const signed = (x: number) => `${x > 0 ? "+" : ""}${Math.round(x).toLocaleString("en-US")}`;
const pct = (x: number | null | undefined) => (x == null ? "" : ` (${x > 0 ? "+" : ""}${Math.round(x * 100)}%)`);

/** "+100% solved (p 0.008)" — the solved-rate difference, with how sure. */
function solvedText(c: SkillEvalCompare | null): string {
  if (!c) return "—";
  const lift = `${c.lift > 0 ? "+" : ""}${Math.round(c.lift * 100)}%`;
  return `${lift} solved (p ${c.p_value < 0.001 ? "<0.001" : c.p_value.toPrecision(2)})`;
}

function tokensText(c: SkillEvalCompare | null): string {
  if (!c) return "—";
  const ci = c.token_delta_ci ? ` [${signed(c.token_delta_ci[0])}, ${signed(c.token_delta_ci[1])}]` : "";
  return `${signed(c.token_delta)} tokens${pct(c.token_change)}${ci}`;
}

export default function SkillEvals({ evalId }: { evalId?: string }) {
  return (
    <div style={{ maxWidth: 860, margin: "0 auto" }}>
      {evalId ? <EvalView evalId={evalId} /> : <EvalList />}
    </div>
  );
}

// ── the list ─────────────────────────────────────────────────────────────────

function EvalList() {
  const qc = useQueryClient();
  const evals = useQuery({ queryKey: ["skill-evals"], queryFn: api.skillEvals });
  const started = useWorkStarted();
  const example = useMutation({
    mutationFn: api.seedSkillEval,
    onSuccess: (r) => { void qc.invalidateQueries({ queryKey: ["skill-evals"] }); navigate(`skill-evals/${r.eval_id}`); },
  });
  const rows = evals.data?.evals ?? [];
  return (
    <>
      <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 6px" }}>Skill evals</h1>
      <div style={{ fontSize: 14, color: C.mut, lineHeight: 1.5, marginBottom: 22 }}>
        Which skills help, and what they cost.
        <HowItWorks>
          <li>Your agent runs the same tasks with no skill, with all, with each alone, and with all but one.</li>
          <li>Solved means the task's tests pass — not that the agent says so.</li>
          <li>Per skill: what it does alone, what it adds to the others, whether the agent opened it, tokens.</li>
        </HowItWorks>
      </div>

      {rows.length > 0 && (
        <div data-testid="skill-eval-list" className="mb-8" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
          {rows.map((r, i) => (
            <button key={r.eval_id} onClick={() => navigate(`skill-evals/${r.eval_id}`)}
              data-testid={`skill-eval-${r.eval_id}`}
              className="flex items-center gap-4 px-4 py-3 w-full flex-wrap"
              style={{ background: "none", border: "none", borderTop: i ? `1px solid ${C.line}` : "none",
                       cursor: "pointer", textAlign: "left" }}>
              <span style={{ fontFamily: MONO, fontSize: 11, color: C.dim, width: 120 }}>
                {localTime(r.created_ts)}
              </span>
              <span className="flex flex-col" style={{ flex: 1, gap: 2 }}>
                {r.example && <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.amber }}>example</span>}
                {r.skills.map((s) => (
                  <span key={s} style={{ fontFamily: MONO, fontSize: 11.5, color: C.text }}>
                    {s} <span style={{ color: verdictColor(r.verdicts[s] ?? "") }}>· {short(r.verdicts[s] ?? "")}</span>
                  </span>
                ))}
              </span>
              <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>
                {r.agent}{r.model ? ` · ${r.model}` : ""} · {r.runs} runs
              </span>
              <ChevronRight size={13} color={C.dim} />
            </button>
          ))}
        </div>
      )}

      {/* the page's question first: does a skill help? Sharing one comes after;
          turning one off is on Try a skill */}
      <HowToRun />
      {!started && !rows.some((r) => r.eval_id === "se-example") && (
        <div className="mt-4" style={{ fontFamily: MONO, fontSize: 12, color: C.mut }}>
          {rows.length === 0 ? "none yet — " : ""}
          <button onClick={() => example.mutate()} data-testid="skill-eval-example"
            style={{ background: "none", border: "none", padding: 0, cursor: "pointer", fontFamily: MONO,
                     fontSize: 12, color: C.steel }}>
            {example.isPending ? "loading…" : "see an example eval →"}
          </button>
        </div>
      )}
      <div data-testid="evals-to-skills" style={{ marginTop: 28, fontFamily: MONO, fontSize: 11.5, color: C.mut }}>
        shared with the team, turned off, checked before installing:{" "}
        <button onClick={() => navigate("skills")} style={{ background: "none", border: "none", padding: 0,
          cursor: "pointer", fontFamily: MONO, fontSize: 11.5, color: C.steel }}>Skills →</button>
      </div>
    </>
  );
}

/** An arm, as a person says it: none → without the skill, all → with it. */
function armWords(arm: string): string {
  if (arm === "none") return "without skills";
  if (arm === "all") return "with the skills";
  if (arm.startsWith("only:")) return `only ${arm.slice(5)}`;
  return arm.startsWith("without:") ? `without ${arm.slice(8)}` : arm;
}

function short(v: string): string {
  return v.split(" — ")[0];
}

function HowToRun() {
  // Run from here: the eval runs on the developer's own machine (their
  // `axor-wrap worker`, in the repository), which takes only jobs its owner
  // queued — a job starts a coding agent and a shell command there.
  const qc = useQueryClient();
  const [skill, setSkill] = useState("./.claude/skills/my-skill");
  const [prompt, setPrompt] = useState("fix the failing test in tests/");
  const [checkCmd, setCheckCmd] = useState("python -m pytest -q");
  // "times each" as typed: a whole number 1–50, or said not to be (QA-027)
  const [trialsText, setTrialsText] = useState("5");
  const trials = /^\d{1,2}$/.test(trialsText.trim()) && Number(trialsText) >= 1 && Number(trialsText) <= 50
    ? Number(trialsText) : null;
  const [picked, setPicked] = useState("");
  // a guest has no account, so no machine: the plane refuses them the list (QA-042)
  const guest = isGuestToken(useApp((s) => s.apiToken));
  const machines = useQuery({ queryKey: ["machines"], queryFn: api.machines, refetchInterval: 10_000,
                              enabled: !guest });
  const mine = (machines.data?.machines ?? []).filter((m) => m.yours && m.online);
  const machine = mine.find((m) => m.machine_id === picked) ?? mine[0];
  const agent = machine?.agents.includes("claude-code") ? "claude-code" : machine?.agents[0] ?? "claude-code";
  const queue = useMutation({
    mutationFn: () => api.queueSkillEval(machine!.machine_id, {
      skills: [skill], agent, trials: trials!, tasks: [{ name: "task-1", prompt, check: checkCmd }] }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["machine-jobs"] }),
  });
  const queueOnce = useOneAtATime(queue);
  const ready = !!machine && trials !== null && skill.trim() && prompt.trim() && checkCmd.trim();
  const field = (labelText: string, value: string, set: (v: string) => void, testId: string) => (
    <label className="flex items-center gap-2" style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>
      <span style={{ width: 110 }}>{labelText}</span>
      <input value={value} onChange={(ev) => set(ev.target.value)} data-testid={testId}
        style={{ flex: 1, background: C.bg, border: `1px solid ${C.line}`, borderRadius: 5, color: C.text,
                 fontFamily: MONO, fontSize: 11.5, padding: "5px 8px", outline: "none" }} />
    </label>
  );
  return (
    <section>
      <div style={label}>DOES A SKILL HELP?</div>
      <div style={{ fontSize: 13.5, color: C.mut, lineHeight: 1.5, marginBottom: 10 }}>
        Your agent does the same task with the skill and without it, a few times each; a command
        says whether the task was done. You see which way solves it more often, and for fewer tokens.
      </div>
      <div className="flex flex-col gap-2 mb-3">
        {field("the skill", skill, setSkill, "eval-skill")}
        {field("a task", prompt, setPrompt, "eval-prompt")}
        {field("done when", checkCmd, setCheckCmd, "eval-check")}
        <label className="flex items-center gap-2 flex-wrap" style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>
          {/* the label keeps its width and its line at 390 (QA-104) */}
          <span data-testid="eval-trials-label" style={{ width: 110, flexShrink: 0, whiteSpace: "nowrap" }}>times each</span>
          <input inputMode="numeric" value={trialsText} data-testid="eval-trials"
            onChange={(ev) => setTrialsText(ev.target.value)}
            style={{ width: 70, background: C.bg, border: `1px solid ${trials === null ? C.red : C.line}`, borderRadius: 5,
                     color: C.text, fontFamily: MONO, fontSize: 11.5, padding: "5px 8px", outline: "none" }} />
          {trials === null
            ? <span style={{ color: C.red }}>a whole number from 1 to 50</span>
            : <span style={{ color: C.dim }}>
                {trials * 2} agent runs on your tokens · 5 each tells a big difference, a small one needs 20+
              </span>}
        </label>
        {mine.length > 0 && (
          <label className="flex items-center gap-2" style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>
            <span style={{ width: 110 }}>on</span>
            <select value={machine?.machine_id ?? ""} onChange={(ev) => setPicked(ev.target.value)}
              data-testid="eval-machine"
              style={{ flex: 1, background: C.bg, border: `1px solid ${C.line}`, borderRadius: 5, color: C.text,
                       fontFamily: MONO, fontSize: 11.5, padding: "5px 8px" }}>
              {mine.map((m) => (
                <option key={m.machine_id} value={m.machine_id}>
                  {m.repo_label || "a repository"} · {m.host} · {m.agents.join(", ") || "no agent found"}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      {mine.length > 0 ? (
        <div className="flex items-center gap-3 flex-wrap">
          <button data-testid="eval-run" disabled={!ready || queue.isPending} onClick={() => queueOnce()}
            style={btn({ color: C.bg, background: C.green, border: `1px solid ${C.green}`, fontSize: 12,
                         fontWeight: 700, padding: "7px 16px" })}>
            {queue.isPending ? "queueing…" : "Run"}
          </button>
          {queue.isError && (
            <span style={{ fontFamily: MONO, fontSize: 11, color: C.red }}>{(queue.error as Error).message}</span>
          )}
        </div>
      ) : (
        <div data-testid="eval-no-machine">
          <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginBottom: 6, lineHeight: 1.6 }}>
            no machine of yours is on line · once, in the repository the eval runs in (it stays open and
            runs what you start here — only yours):
          </div>
          {/* the same install as Connect and Skills give, with the extra the worker needs */}
          <MachineCommand testId="skill-eval-cmd"
            cmd={"uv tool install --upgrade 'axor-wrap[plane,eval]'\naxor-wrap worker"} />
        </div>
      )}
      <Jobs />
    </section>
  );
}

const ACTIVE = new Set(["queued", "running"]);

/** The jobs queued from here: how far along, and where the report is. */
function Jobs() {
  const qc = useQueryClient();
  const guest = isGuestToken(useApp((s) => s.apiToken));
  const jobs = useQuery({
    queryKey: ["machine-jobs"], queryFn: api.machineJobs, enabled: !guest,
    refetchInterval: (q) => ((q.state.data?.jobs ?? []).some((j) => ACTIVE.has(j.state)) ? 3000 : false),
  });
  const cancel = useMutation({
    mutationFn: api.cancelMachineJob,
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["machine-jobs"] }),
  });
  const list = jobs.data?.jobs ?? [];
  // a job that finished posted a report: the list above should show it — once
  // per newly finished job, not on every render
  const finished = list.filter((j) => j.state === "done").map((j) => j.job_id).join(",");
  useEffect(() => {
    if (finished) void qc.invalidateQueries({ queryKey: ["skill-evals"] });
  }, [finished, qc]);
  if (!list.length) return null;
  return (
    <div data-testid="eval-jobs" className="flex flex-col gap-2" style={{ marginTop: 14 }}>
      {list.map((j) => {
        const p = j.progress;
        const color = j.state === "done" ? C.green : j.state === "running" || j.state === "queued" ? C.steel
          : j.state === "cancelled" ? C.dim : C.red;
        return (
          <div key={j.job_id} data-testid={`eval-job-${j.job_id}`} className="flex items-center gap-3 flex-wrap"
            style={{ fontFamily: MONO, fontSize: 11, color: C.mut, border: `1px solid ${C.line}`, borderRadius: 6,
                     padding: "7px 10px" }}>
            <span style={{ color, minWidth: 70 }}>{j.state === "running" && j.cancel_requested ? "stopping" : j.state}</span>
            <span style={{ color: C.text }}>{j.spec.skills.join(", ")}</span>
            {j.state === "queued" && <span>waiting for the machine to take it</span>}
            {j.state === "running" && (
              <span>{p.done ?? 0} / {p.total || "…"} runs · {p.solved ?? 0} solved</span>
            )}
            {j.error && j.state !== "done" && <span style={{ color: C.dim }}>{j.error}</span>}
            <span style={{ flex: 1 }} />
            {ACTIVE.has(j.state) && !j.cancel_requested && (
              <button data-testid={`eval-job-cancel-${j.job_id}`} onClick={() => cancel.mutate(j.job_id)}
                style={btn({ color: C.mut, fontSize: 10.5, padding: "3px 10px" })}>cancel</button>
            )}
            {j.state === "done" && j.eval_id && (
              <button data-testid={`eval-job-report-${j.job_id}`} onClick={() => navigate(`skill-evals/${j.eval_id}`)}
                style={{ background: "none", border: "none", padding: 0, cursor: "pointer", fontFamily: MONO,
                         fontSize: 11, color: C.steel }}>the report →</button>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── one eval ─────────────────────────────────────────────────────────────────

function EvalView({ evalId }: { evalId: string }) {
  const ev = useQuery({ queryKey: ["skill-eval", evalId], queryFn: () => api.skillEval(evalId) });
  if (ev.isLoading) return <Loader2 size={16} className="animate-spin" color={C.dim} />;
  if (!ev.data) {
    return <div style={{ fontFamily: MONO, fontSize: 12, color: C.mut }}>no skill eval {evalId}</div>;
  }
  const e: SkillEval = ev.data;
  const trials = e.overall.arms[0]?.trials ?? 0;
  return (
    <div data-testid="skill-eval">
      <button onClick={() => navigate("skill-evals")} style={btn({ color: C.dim, fontSize: 10.5, padding: "3px 10px", marginBottom: 14 })}>
        ← all skill evals
      </button>
      <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 4px" }}>
        {e.skills.length} skill{e.skills.length === 1 ? "" : "s"}, {e.per_task.length} task{e.per_task.length === 1 ? "" : "s"}
      </h1>
      <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.dim, marginBottom: 18 }}>
        {e.agent}{e.model ? ` · ${e.model}` : ""} · {e.arms.length} ways × {trials} times each · {localTime(e.created_ts)}
        {e.example && <span style={{ color: C.amber }}> · example: a scripted agent, not a real run</span>}
      </div>

      <div style={label}>PER SKILL</div>
      <div className="flex flex-col gap-3 mb-8">
        {e.per_skill.map((s) => <SkillCard key={s.skill} s={s} single={e.skills.length === 1} example={!!e.example} />)}
      </div>

      {/* nothing solved, with the skill or without: the "done when" command is
          the question, not the skill */}
      {e.overall.arms.length > 0 && e.overall.arms.every((a) => a.solved === 0) && (
        <div data-testid="eval-nothing-solved" className="mb-6 p-3" style={{ border: `1px solid ${C.amber}`, borderRadius: 8,
          fontFamily: MONO, fontSize: 11.5, color: C.amber, lineHeight: 1.6 }}>
          Nothing was solved, with the skill or without: the “done when” command never passed — this says
          nothing about the skill yet. Run that command in the repository to see why.
          {(() => {
            const out = String((e.trials ?? []).map((t) => (t as { check_output?: string }).check_output ?? "")
              .find((x) => x) ?? "");
            return out ? <pre style={{ color: C.mut, marginTop: 6, whiteSpace: "pre-wrap", fontSize: 10.5 }}>{out.slice(-400)}</pre> : null;
          })()}
        </div>
      )}
      <Headline task={e.overall} />

      <div style={label}>WITH THE SKILL AND WITHOUT IT — ALL TASKS</div>
      <ArmTable t={e.overall} />
      {e.per_task.length > 1 && e.per_task.map((t) => <TaskFold key={t.task} t={t} />)}

      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 14, lineHeight: 1.7 }}>
        solved = the task's "done when" command passed after the agent finished · tokens = what the
        agent reported using · tokens / solved = all of a way's tokens over the tasks it solved, so a
        cheap way that solves less is not called cheap · p = how likely a difference this size is by
        chance (under 0.05: probably real) · [ ] = the range the true difference likely falls in ·
        opened = the agent called the skill or read a file under it
      </div>
    </div>
  );
}

function SkillCard({ s, single, example = false }: { s: SkillEvalSkill; single: boolean; example?: boolean }) {
  return (
    <div data-testid={`skill-card-${s.skill}`} className="p-4"
      style={{ background: C.panel, border: `1px solid ${C.line}`, borderLeft: `3px solid ${verdictColor(s.verdict)}`, borderRadius: 8 }}>
      <div className="flex items-baseline gap-3 flex-wrap mb-2">
        <span style={{ fontFamily: MONO, fontSize: 14, color: C.text }}>{s.skill}</span>
        <span style={{ fontSize: 14, fontWeight: 600, color: verdictColor(s.verdict) }}>{s.verdict}</span>
        <span style={{ flex: 1 }} />
        {/* in the example eval these would turn the REAL team's skills on and
            off — a click in a demo changing the team (QA-005) */}
        {!example && <TeamSwitch name={s.skill} />}
        {!example && <SkillSwitch name={s.skill} />}
      </div>
      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginBottom: 8 }}>
        opened {s.opened} · ~{s.carried_tokens} tokens in every context while installed, ~{s.body_tokens} when read
      </div>
      <div className="flex gap-6 flex-wrap" style={{ fontFamily: MONO, fontSize: 11.5, lineHeight: 1.7 }}>
        <div>
          <div style={{ color: C.dim, fontSize: 10 }}>{single ? "WITH IT VS NONE" : "ALONE VS NONE"}</div>
          <div style={{ color: C.text }}>{solvedText(s.alone)}</div>
          <div style={{ color: C.mut }}>{tokensText(s.alone)}</div>
        </div>
        {!single && (
          <div>
            <div style={{ color: C.dim, fontSize: 10 }}>ON TOP OF THE OTHERS (ALL VS ALL BUT IT)</div>
            <div style={{ color: C.text }}>{solvedText(s.on_top)}</div>
            <div style={{ color: C.mut }}>{tokensText(s.on_top)}</div>
          </div>
        )}
      </div>
    </div>
  );
}

/** The one sentence a reader wants: the cheapest arm, per solved task. */
function Headline({ task }: { task: SkillEvalTask }) {
  const solving = task.arms.filter((a) => a.tokens_per_solved != null);
  if (solving.length === 0) return null;
  const best = solving.reduce((a, b) => ((a.tokens_per_solved ?? Infinity) <= (b.tokens_per_solved ?? Infinity) ? a : b));
  const none = task.arms.find((a) => a.arm === "none");
  return (
    <div data-testid="skill-eval-headline" style={{ fontSize: 15, lineHeight: 1.6, marginBottom: 18 }}>
      Fewest tokens per solved task: <span style={{ fontFamily: MONO, color: C.green }}>{armWords(best.arm)}</span> —{" "}
      {n(best.tokens_per_solved)} tokens, {best.solved}/{best.trials} solved
      {none && (
        <span style={{ color: C.mut }}>
          {" "}· no skill: {none.tokens_per_solved == null ? `nothing solved (${none.solved}/${none.trials})`
            : `${n(none.tokens_per_solved)} tokens, ${none.solved}/${none.trials} solved`}
        </span>
      )}
    </div>
  );
}

function ArmTable({ t }: { t: SkillEvalTask }) {
  const vs = Object.fromEntries(t.vs_none.map((c) => [c.arm, c]));
  const th: React.CSSProperties = { fontFamily: MONO, fontSize: 10, color: C.dim, fontWeight: 400, textAlign: "left", padding: "6px 8px", whiteSpace: "nowrap" };
  const td: React.CSSProperties = { fontFamily: MONO, fontSize: 11.5, color: C.mut, padding: "6px 8px", borderTop: `1px solid ${C.line}`, whiteSpace: "nowrap" };
  return (
    <div style={{ overflowX: "auto", background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
      <table data-testid="skill-eval-arms" style={{ borderCollapse: "collapse", width: "100%" }}>
        <thead>
          <tr>
            {["way", "solved", "vs no skill", "tokens / run", "Δ tokens vs none [95%]", "$ / run", "tokens / solved", "turns"].map((h) => (
              <th key={h} style={th}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {t.arms.map((a: SkillEvalArm) => {
            const c = vs[a.arm];
            return (
              <tr key={a.arm}>
                <td style={{ ...td, color: C.text }}>{armWords(a.arm)}</td>
                <td style={{ ...td, color: a.solved === a.trials ? C.green : a.solved === 0 ? C.red : C.text }}>{a.solved}/{a.trials}</td>
                <td style={td}>{c ? solvedText(c).replace(" solved", "") : "—"}</td>
                <td style={td}>{n(a.mean_tokens)}</td>
                <td style={td}>{c ? tokensText(c).replace(" tokens", "") : "—"}</td>
                <td style={td}>{usd(a.mean_cost_usd)}</td>
                <td style={{ ...td, color: C.text }}>{n(a.tokens_per_solved)}</td>
                <td style={td}>{a.mean_turns}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function TaskFold({ t }: { t: SkillEvalTask }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-3">
      <button onClick={() => setOpen(!open)} style={{ background: "none", border: "none", color: C.mut, fontFamily: MONO,
        fontSize: 12, cursor: "pointer", display: "flex", alignItems: "center", gap: 6, padding: "6px 0" }}>
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />} {t.task}
      </button>
      {open && <ArmTable t={t} />}
    </div>
  );
}
