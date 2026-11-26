// Skills turned off for the team. The plane keeps the list; every developer's
// axor sidecar fetches it and refuses the agent's use of those skills — the
// Skill call, and any read or command under the skill's folder. It cannot
// delete the folder: the skill's name and description stay in the agent's
// context until someone removes it, and that is said where the switch is.
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, TeamSkill } from "../api";
import { C, MONO, btn } from "../theme";
import { HowItWorks, MachineCommand } from "./MachineCommand";
import { useWorkStarted } from "../workStarted";

export function useSkillsOff() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["coding-skills"], queryFn: api.codingSkills, refetchInterval: 15000 });
  const set = useMutation({
    mutationFn: ({ name, off }: { name: string; off: boolean }) => api.setSkillOff(name, off),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["coding-skills"] }),
  });
  const team = useMutation({
    mutationFn: ({ name, on }: { name: string; on: boolean }) => api.setSkillTeam(name, on),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["coding-skills"] }),
  });
  const off = new Set((q.data?.off ?? []).map((s) => s.skill));
  return { off, attempts: q.data?.attempts ?? {}, list: q.data?.off ?? [], set,
           error: (set.error ?? team.error) as Error | null, shared: q.data?.team ?? [], team };
}

const day = (ts: string) => ts.slice(0, 16).replace("T", " ");

// the plane's rule for a skill's name (coding_api._NAME): checked here before
// sending, since a "/" in it never reaches the route at all (QA-026)
const SKILL_NAME = /^[A-Za-z0-9_.:-]{1,64}$/;

/** Shared skills waiting for someone's decision: never turned on, or a newer
 * upload than the version on — the menu says how many. */
export function usePendingSkills(): number {
  const q = useQuery({ queryKey: ["coding-skills"], queryFn: api.codingSkills, refetchInterval: 30000, retry: false });
  const off = new Set((q.data?.off ?? []).map((o) => o.skill));
  return (q.data?.team ?? []).filter((t) => !off.has(t.name)
    && (!t.live || t.live.version !== t.latest.version)).length;
}

/** On a skill a team member shared: on for the team, or a newer upload. */
export function TeamSwitch({ name }: { name: string }) {
  const { shared, team, off, set } = useSkillsOff();
  const [sure, setSure] = useState(false);
  const s = shared.find((t) => t.name === name);
  if (!s || off.has(name)) return null;
  if (s.example) {
    // the product's example: turning it on would hand it to every developer's
    // agent — the plane refuses it, and the page does not offer it (QA-005)
    return (
      <span data-testid={`team-example-${name}`} style={{ fontFamily: MONO, fontSize: 11, color: C.amber }}>
        example · share your own to turn one on for the team
      </span>
    );
  }
  const danger = s.latest.check?.summary.danger ?? 0;
  const newer = s.live && s.live.version !== s.latest.version;
  if (s.live && !newer) {
    return (
      <span data-testid={`team-is-on-${name}`} style={{ fontFamily: MONO, fontSize: 11, color: C.green }}>
        on for the team ·{" "}
        <button onClick={() => team.mutate({ name, on: false })} style={link}>take off the team</button>
      </span>
    );
  }
  // rejecting it turns it off for the team: no agent may use it, and it no
  // longer waits for a decision
  const reject = (
    <button data-testid={`team-reject-${name}`} onClick={() => set.mutate({ name, off: true })}
      disabled={set.isPending} style={btn({ color: danger ? C.red : C.mut, borderColor: danger ? C.red : C.line,
                                            fontSize: 10.5, padding: "3px 10px" })}>
      reject
    </button>
  );
  if (danger && !sure) {
    // a skill the check found dangerous is not one click from every machine
    return (
      <span className="flex items-center gap-2">
        {reject}
        <button data-testid={`team-on-${name}`} onClick={() => setSure(true)}
          style={btn({ color: C.mut, fontSize: 10.5, padding: "3px 10px" })}>turn on anyway…</button>
      </span>
    );
  }
  return (
    <span className="flex items-center gap-2 flex-wrap">
      {danger > 0 && (
        <span data-testid={`team-confirm-${name}`} style={{ fontFamily: MONO, fontSize: 10.5, color: C.red }}>
          {danger} dangerous in its files — every developer's agent gets them
        </span>
      )}
      {!danger && !s.live && reject}
      <button data-testid={danger ? `team-on-confirm-${name}` : `team-on-${name}`}
        onClick={() => team.mutate({ name, on: true }, { onSuccess: () => setSure(false) })}
        disabled={team.isPending} style={btn({ color: danger ? C.red : C.green, borderColor: danger ? C.red : C.green,
                                                fontSize: 10.5, padding: "3px 10px" })}>
        {danger ? "turn it on for the team" : newer ? "turn the new version on for the team" : "turn on for the team"}
      </button>
      {danger > 0 && <button onClick={() => setSure(false)} style={link}>cancel</button>}
    </span>
  );
}

function CheckLine({ s }: { s: TeamSkill }) {
  const c = s.latest.check;
  if (!c) {
    return <span style={{ color: C.dim }}>not read on this plane (its axor-wrap has no skill check)</span>;
  }
  const { danger, warn } = c.summary;
  if (!danger && !warn) return <span style={{ color: C.green }}>nothing risky in its files</span>;
  return (
    <span style={{ color: danger ? C.red : C.amber }}>
      {danger ? `${danger} dangerous` : ""}{danger && warn ? " · " : ""}{warn ? `${warn} to look at` : ""}
      {" — "}{c.findings.filter((f) => f.severity !== "info").slice(0, 2).map((f) => f.what).join("; ")}
    </span>
  );
}

/** Skills shared for the team: what each is, whether it is on, and how to
 * share one. On, every developer's sidecar installs it. */
export function TeamSkillsPanel() {
  const qc = useQueryClient();
  const { shared } = useSkillsOff();
  const [open, setOpen] = useState<string | null>(null);
  const started = useWorkStarted();
  const example = useMutation({
    mutationFn: api.seedTeamSkill,
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["coding-skills"] }),
  });
  return (
    <section data-testid="team-skills" className="mb-8">
      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, letterSpacing: "0.1em", marginBottom: 8 }}>
        SHARED WITH THE TEAM
      </div>
      <div style={{ fontSize: 13.5, color: C.mut, lineHeight: 1.5, marginBottom: 10 }}>
        Turn a skill on — every developer's agent gets it. Take it off — it's removed.
        <HowItWorks testId="team-skills-how">
          <li>Arrives within 30 seconds, or when a session starts.</li>
          <li>Stays at the version you turned on. A new upload waits until you turn it on again.</li>
          <li>A developer's own skill with the same name is never overwritten.</li>
          <li>After turning one on, run a context health check.</li>
        </HowItWorks>
      </div>
      {shared.length > 0 && (
        <div className="mb-3" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
          {shared.map((s, i) => (
            <div key={s.name} data-testid={`team-row-${s.name}`} className="px-4 py-3"
              style={{ borderTop: i ? `1px solid ${C.line}` : "none" }}>
              <div className="flex items-center gap-3 flex-wrap">
                <span style={{ fontFamily: MONO, fontSize: 12.5, color: C.text }}>{s.name}</span>
                <span style={{ fontSize: 12.5, color: C.mut, flex: 1, minWidth: 200 }}>{s.description}</span>
                <TeamSwitch name={s.name} />
              </div>
              <div className="flex gap-3 flex-wrap" style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 6, lineHeight: 1.6 }}>
                <span>
                  {s.live ? `on since ${day(s.live.ts)}${s.live.by ? ` · by ${s.live.by}` : ""}`
                    : `shared ${day(s.latest.ts)}${s.latest.by ? ` by ${s.latest.by}` : ""} · not on`}
                </span>
                {s.live && s.live.version !== s.latest.version && (
                  <span style={{ color: C.amber }}>a newer upload {day(s.latest.ts)} — the team still has the earlier one</span>
                )}
                <span>{s.latest.files.length} file{s.latest.files.length === 1 ? "" : "s"}</span>
                <CheckLine s={s} />
                <button onClick={() => setOpen(open === s.name ? null : s.name)} style={{ ...link, fontSize: 10.5 }}>
                  {open === s.name ? "hide SKILL.md" : "read SKILL.md"}
                </button>
              </div>
              {open === s.name && (
                <pre data-testid={`team-md-${s.name}`} style={{ background: C.bg, border: `1px solid ${C.line}`, borderRadius: 6,
                  padding: "10px 12px", margin: "8px 0 0", fontFamily: MONO, fontSize: 11, color: C.text,
                  whiteSpace: "pre-wrap", lineHeight: 1.55 }}>{s.skill_md}</pre>
              )}
            </div>
          ))}
        </div>
      )}
      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginBottom: 6 }}>share one</div>
      <MachineCommand testId="team-share-cmd" cmd="axor-wrap skill share ./.claude/skills/my-skill" />
      {!shared.length && !started && (
        <button onClick={() => example.mutate()} data-testid="team-skill-example"
          style={{ ...link, fontSize: 11.5, marginTop: 8 }}>
          {example.isPending ? "loading…" : "see an example →"}
        </button>
      )}
    </section>
  );
}

const link: React.CSSProperties = {
  background: "none", border: "none", padding: 0, cursor: "pointer", fontFamily: MONO, fontSize: 11, color: C.steel,
};

/** The switch on one skill. */
export function SkillSwitch({ name }: { name: string }) {
  const { off, attempts, set, shared } = useSkillsOff();
  const isOff = off.has(name);
  // on for the team, the way out is "take off the team" beside it
  if (!isOff && shared.some((t) => t.name === name && t.live)) return null;
  if (!isOff) {
    return (
      <button data-testid={`skill-off-${name}`} onClick={() => set.mutate({ name, off: true })}
        disabled={set.isPending} style={btn({ color: C.mut, fontSize: 10.5, padding: "3px 10px" })}>
        turn off for the team
      </button>
    );
  }
  return (
    <span data-testid={`skill-is-off-${name}`} style={{ fontFamily: MONO, fontSize: 11, color: C.amber }}>
      turned off for the team{attempts[name] ? ` · ${attempts[name]} refused` : ""} ·{" "}
      <button onClick={() => set.mutate({ name, off: false })} style={link}>turn back on</button>
    </span>
  );
}

/** The list, with a way to turn off a skill by name. */
export function SkillsOffPanel() {
  const { list, attempts, set, error } = useSkillsOff();
  const [name, setName] = useState("");
  const n = name.trim();
  const bad = !!n && !SKILL_NAME.test(n);
  const add = () => {
    if (n && !bad) set.mutate({ name: n, off: true }, { onSuccess: () => setName("") });
  };
  return (
    <section data-testid="skills-off" className="mb-8">
      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, letterSpacing: "0.1em", marginBottom: 8 }}>
        TURNED OFF FOR THE TEAM
      </div>
      <div style={{ fontSize: 13.5, color: C.mut, lineHeight: 1.5, marginBottom: 10 }}>
        No agent on the team can use it: no calls, no reading its files.
        <HowItWorks>
          <li>Takes effect within 30 seconds.</li>
          <li>The agent still sees its name until the skill's folder is deleted.</li>
          <li>Covers agents connected to Axor.</li>
        </HowItWorks>
      </div>
      {list.length > 0 && (
        <div className="mb-3" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
          {list.map((s, i) => (
            <div key={s.skill} data-testid={`off-row-${s.skill}`} className="flex items-center gap-3 px-4 py-2 flex-wrap"
              style={{ borderTop: i ? `1px solid ${C.line}` : "none", fontFamily: MONO, fontSize: 11.5 }}>
              <span style={{ color: C.text }}>{s.skill}</span>
              <span style={{ color: C.dim }}>
                since {s.ts.slice(0, 16).replace("T", " ")}{s.by ? ` · by ${s.by}` : ""}
              </span>
              <span style={{ color: attempts[s.skill] ? C.amber : C.dim }}>
                {attempts[s.skill] ?? 0} refused attempt{attempts[s.skill] === 1 ? "" : "s"}
              </span>
              <span style={{ flex: 1 }} />
              <button onClick={() => set.mutate({ name: s.skill, off: false })} style={link}>turn back on</button>
            </div>
          ))}
        </div>
      )}
      <div className="flex gap-2 items-center flex-wrap">
        <input value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && add()}
          placeholder="skill name ⏎" aria-label="skill to turn off"
          style={{ width: 200, background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: C.text,
                   fontFamily: MONO, fontSize: 11.5, padding: "5px 8px", outline: "none" }} />
        <button onClick={add} disabled={!n || bad || set.isPending}
          style={btn({ color: n && !bad ? C.text : C.dim, fontSize: 11, padding: "5px 10px" })}>turn off</button>
        {bad
          ? <span style={{ fontFamily: MONO, fontSize: 11, color: C.red }}>a skill name is a plain id (letters, digits, - _ . :), at most 64</span>
          : error && <span style={{ fontFamily: MONO, fontSize: 11, color: C.red }}>{error.message}</span>}
      </div>
    </section>
  );
}
