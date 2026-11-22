// The team's rules for coding agents (Claude Code, Codex): what the hooks
// refuse on every developer's machine, whoever asked — decided once here by an
// admin. A repository may refuse more (.axor/rules.json "refuse"), never allow
// what the team refuses. Until an admin saves, each repository decides.
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useApp } from "../store";
import { C, MONO, btn } from "../theme";
import { errorWords } from "../refusal";

export default function CodingPolicy() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["coding-rules"], queryFn: api.codingRules });
  const email = useApp((s) => s.identityEmail);
  const me = useQuery({ queryKey: ["identity-me"], queryFn: api.identityMe, enabled: !!email, retry: false });
  const role = me.data?.memberships.find((m) => m.org_id === me.data?.active_org)?.role ?? me.data?.role;
  // an open plane has no roles: whoever runs it decides
  const canEdit = !email || role === "owner" || role === "admin";
  const [allow, setAllow] = useState<Set<string>>(new Set());
  const [mode, setMode] = useState<"enforce" | "observe">("enforce");
  useEffect(() => {
    if (q.data) { setAllow(new Set(q.data.allow)); setMode(q.data.mode); }
  }, [q.data]);
  const save = useMutation({
    mutationFn: () => api.setCodingRules([...allow], mode),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["coding-rules"] }),
  });
  const d = q.data;
  const changed = !!d && (mode !== d.mode || [...allow].sort().join() !== [...d.allow].sort().join() || !d.set);
  const toggle = (id: string) => setAllow((cur) => {
    const next = new Set(cur);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  return (
    <div data-testid="coding-policy" style={{ maxWidth: 820, margin: "0 auto" }}>
      <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 6px" }}>Policy for coding agents</h1>
      <div style={{ fontSize: 14, color: C.mut, lineHeight: 1.6, marginBottom: 18 }}>
        What every developer's Claude Code and Codex is refused, whoever asked — the same rules on
        every machine of the team. A repository may refuse more; it cannot allow what is refused here.
      </div>
      {d && !d.set && (
        <div data-testid="policy-not-set" className="mb-4 p-3" style={{ border: `1px solid ${C.line}`, borderRadius: 8,
          fontFamily: MONO, fontSize: 11.5, color: C.mut, lineHeight: 1.6 }}>
          No team policy yet: each repository decides with its own .axor/rules.json, and everything below
          is refused by default. Saving makes this the team's policy.
        </div>
      )}
      <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
        {(d?.catalog ?? []).map((r, i) => {
          const allowed = allow.has(r.id) && !r.fixed;
          return (
            <div key={r.id} data-testid={`rule-${r.id}`} className="flex items-center gap-3 px-4 py-3 flex-wrap"
              style={{ borderTop: i ? `1px solid ${C.line}` : "none" }}>
              <div style={{ flex: 1, minWidth: 260 }}>
                <div style={{ fontSize: 13.5, color: C.text }}>{r.what}</div>
                <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 2 }}>{r.why}</div>
              </div>
              {r.fixed ? (
                <span style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>always refused</span>
              ) : (
                <div className="flex gap-1" role="radiogroup" aria-label={r.what}>
                  {(["refused", "allowed"] as const).map((v) => {
                    const on = (v === "allowed") === allowed;
                    return (
                      <button key={v} role="radio" aria-checked={on} disabled={!canEdit}
                        data-testid={`rule-${r.id}-${v}`}
                        onClick={() => { if ((v === "allowed") !== allowed) toggle(r.id); }}
                        style={btn({ fontSize: 11, padding: "3px 10px",
                          color: on ? (v === "refused" ? C.green : C.amber) : C.dim,
                          borderColor: on ? (v === "refused" ? C.green : C.amber) : C.line })}>
                        {v}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>
      <div className="mt-4 flex items-center gap-3 flex-wrap" style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut }}>
        <span>the hooks</span>
        {([["enforce", "refuse it"], ["observe", "only record it (nothing is refused)"]] as const).map(([v, words]) => (
          <label key={v} className="flex items-center gap-1" style={{ cursor: canEdit ? "pointer" : "default" }}>
            <input type="radio" name="policy-mode" checked={mode === v} disabled={!canEdit}
              onChange={() => setMode(v)} data-testid={`policy-mode-${v}`} />
            {words}
          </label>
        ))}
      </div>
      <div className="mt-4 flex items-center gap-3 flex-wrap" style={{ fontFamily: MONO, fontSize: 11.5 }}>
        {canEdit ? (
          <button data-testid="policy-save" onClick={() => save.mutate()} disabled={!changed || save.isPending}
            style={btn({ color: changed ? C.text : C.dim, borderColor: changed ? C.steel : C.line, fontSize: 12, padding: "6px 14px" })}>
            {save.isPending ? "…" : "save for the team"}
          </button>
        ) : (
          <span style={{ color: C.dim }}>an owner or admin changes the team's policy</span>
        )}
        {d?.set && <span style={{ color: C.dim }}>the team's policy · by {d.by || "—"} · {d.ts.slice(0, 16).replace("T", " ")}</span>}
        {save.isSuccess && <span style={{ color: C.green }}>saved — every sidecar picks it up within a minute</span>}
        {save.isError && <span style={{ color: C.red }}>{errorWords((save.error as Error).message)}</span>}
      </div>
    </div>
  );
}
