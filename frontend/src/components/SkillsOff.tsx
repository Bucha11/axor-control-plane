// Skills turned off for the team. The plane keeps the list; every developer's
// axor sidecar fetches it and refuses the agent's use of those skills — the
// Skill call, and any read or command under the skill's folder. It cannot
// delete the folder: the skill's name and description stay in the agent's
// context until someone removes it, and that is said where the switch is.
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { C, MONO, btn } from "../theme";

export function useSkillsOff() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["coding-skills"], queryFn: api.codingSkills, refetchInterval: 15000 });
  const set = useMutation({
    mutationFn: ({ name, off }: { name: string; off: boolean }) => api.setSkillOff(name, off),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["coding-skills"] }),
  });
  const off = new Set((q.data?.off ?? []).map((s) => s.skill));
  return { off, attempts: q.data?.attempts ?? {}, list: q.data?.off ?? [], set, error: set.error as Error | null };
}

const link: React.CSSProperties = {
  background: "none", border: "none", padding: 0, cursor: "pointer", fontFamily: MONO, fontSize: 11, color: C.steel,
};

/** The switch on one skill. */
export function SkillSwitch({ name }: { name: string }) {
  const { off, attempts, set } = useSkillsOff();
  const isOff = off.has(name);
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
  const add = () => {
    const n = name.trim();
    if (n) set.mutate({ name: n, off: true }, { onSuccess: () => setName("") });
  };
  return (
    <section data-testid="skills-off" className="mb-8">
      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, letterSpacing: "0.1em", marginBottom: 8 }}>
        TURNED OFF FOR THE TEAM
      </div>
      <div style={{ fontSize: 13, color: C.mut, lineHeight: 1.6, marginBottom: 10 }}>
        Every developer's agent is refused a turned-off skill — calling it, reading its files, running
        its scripts — within about 30 seconds. Its name and description stay in the agent's context
        until the folder is removed (<span style={{ fontFamily: MONO, color: C.text }}>.claude/skills/&lt;name&gt;</span>);
        only agents with the axor hooks are covered.
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
        <button onClick={add} disabled={!name.trim() || set.isPending}
          style={btn({ color: name.trim() ? C.text : C.dim, fontSize: 11, padding: "5px 10px" })}>turn off</button>
        {error && <span style={{ fontFamily: MONO, fontSize: 11, color: C.red }}>{error.message}</span>}
      </div>
    </section>
  );
}
