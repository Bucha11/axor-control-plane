// Skill evals — which of your coding agent's skills help, and what they cost.
//
// The eval itself runs on the developer's machine (axor-eval: the same tasks
// with no skill, all, each alone and all but one; solved = the task's tests
// passed), and `--plane` posts its report here. This page reads that report as
// written: per skill, what it does alone and on top of the others, whether the
// agent opened it at all, and its token cost; per arm, solved and spend.
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Loader2 } from "lucide-react";
import { api, SkillEval, SkillEvalArm, SkillEvalCompare, SkillEvalSkill, SkillEvalTask } from "../api";
import { navigate } from "../router";
import { C, MONO, btn } from "../theme";
import { SkillSwitch, TeamSkillsPanel, TeamSwitch } from "../components/SkillsOff";
import { HowItWorks, MachineCommand } from "../components/MachineCommand";

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
                {r.created_ts.slice(0, 16).replace("T", " ")}
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
      {!rows.some((r) => r.eval_id === "se-example") && (
        <div className="mt-4" style={{ fontFamily: MONO, fontSize: 12, color: C.mut }}>
          {rows.length === 0 ? "none yet — " : ""}
          <button onClick={() => example.mutate()} data-testid="skill-eval-example"
            style={{ background: "none", border: "none", padding: 0, cursor: "pointer", fontFamily: MONO,
                     fontSize: 12, color: C.steel }}>
            {example.isPending ? "loading…" : "see an example eval →"}
          </button>
        </div>
      )}
      <div style={{ marginTop: 36 }}><TeamSkillsPanel /></div>
    </>
  );
}

/** An arm, as a person says it: none → without the skill, all → with it. */
function armWords(arm: string): string {
  if (arm === "none") return "without skills";
  if (arm === "all") return "with the skills";
  return arm.startsWith("only:") ? `only ${arm.slice(5)}` : arm;
}

function short(v: string): string {
  return v.split(" — ")[0];
}

function HowToRun() {
  // the file is written here, from four answers — not by hand from a sample
  const [skill, setSkill] = useState("./.claude/skills/my-skill");
  const [prompt, setPrompt] = useState("fix the failing test in tests/");
  const [checkCmd, setCheckCmd] = useState("python -m pytest -q");
  const [trials, setTrials] = useState(5);
  const runs = trials * 2;
  const spec = JSON.stringify({
    skills: [skill], repo: ".", agent: "claude-code", trials,
    tasks: [{ name: "task-1", prompt, check: checkCmd }],
  }, null, 2);
  const cmd = `pip install --upgrade axor-eval\ncat > skill-eval.json <<'EOF'\n${spec}\nEOF\npython -m axor_eval.skills run skill-eval.json --plane`;
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
        <label className="flex items-center gap-2" style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>
          <span style={{ width: 110 }}>times each</span>
          <input type="number" min={1} max={50} value={trials} data-testid="eval-trials"
            onChange={(ev) => setTrials(Math.max(1, Math.min(50, Number(ev.target.value) || 1)))}
            style={{ width: 70, background: C.bg, border: `1px solid ${C.line}`, borderRadius: 5, color: C.text,
                     fontFamily: MONO, fontSize: 11.5, padding: "5px 8px", outline: "none" }} />
          <span style={{ color: C.dim }}>
            {runs} agent runs on your tokens · 5 each tells a big difference, a small one needs 20+
          </span>
        </label>
      </div>
      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginBottom: 6 }}>in the repository, on a machine connected to this plane:</div>
      <MachineCommand testId="skill-eval-cmd" cmd={cmd} />
    </section>
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
        {e.agent}{e.model ? ` · ${e.model}` : ""} · {e.arms.length} ways × {trials} times each · {e.created_ts.slice(0, 16).replace("T", " ")}
        {e.example && <span style={{ color: C.amber }}> · example: a scripted agent, not a real run</span>}
      </div>

      <div style={label}>PER SKILL</div>
      <div className="flex flex-col gap-3 mb-8">
        {e.per_skill.map((s) => <SkillCard key={s.skill} s={s} single={e.skills.length === 1} />)}
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

function SkillCard({ s, single }: { s: SkillEvalSkill; single: boolean }) {
  return (
    <div data-testid={`skill-card-${s.skill}`} className="p-4"
      style={{ background: C.panel, border: `1px solid ${C.line}`, borderLeft: `3px solid ${verdictColor(s.verdict)}`, borderRadius: 8 }}>
      <div className="flex items-baseline gap-3 flex-wrap mb-2">
        <span style={{ fontFamily: MONO, fontSize: 14, color: C.text }}>{s.skill}</span>
        <span style={{ fontSize: 14, fontWeight: 600, color: verdictColor(s.verdict) }}>{s.verdict}</span>
        <span style={{ flex: 1 }} />
        <TeamSwitch name={s.skill} />
        <SkillSwitch name={s.skill} />
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
