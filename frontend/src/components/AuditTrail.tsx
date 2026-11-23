// The workspace's audit trail: who paused, stopped or capped which agent, who
// pulled the team's brake, granted an exception, changed the rules, the seat
// limit, a skill — and the keys minted and revoked. Filtered here, exported as
// CSV or JSON lines for an archive. Admins only (the plane says so with 403).
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type AuditItem } from "../api";
import { C, MONO, btn } from "../theme";
import { errorWords } from "../refusal";

// an act, as a person says it
const WORDS: Record<string, string> = {
  pause: "paused", resume: "resumed", stop: "stopped", cascade_stop: "stopped with everything it started",
  budget: "capped the calls of", excise: "cut context from", inject: "injected into",
  replan: "asked to replan", experiment: "armed an experiment on", say: "said to the agent of",
  brake_on: "pulled the team's brake on", brake_off: "released the team's brake on",
  rule_allowed: "allowed the rule", rule_refused: "refused again the rule", rules_mode: "set the rules to",
  exception_granted: "granted an exception", exception_withdrawn: "withdrew the exception",
  source_trusted: "trusted the source", source_untrusted: "stopped trusting",
  skill_off: "turned off the skill", skill_on: "turned back on the skill",
  skill_shared_on: "shared with the team the skill", skill_shared_off: "took off the team the skill",
  pair_open: "opened a pair on", pair_end: "ended the pair on", pair_let_in: "let a teammate pair on",
  pair_declined: "declined a pair on", pair_note: "sent a note to",
  incident_settled: "settled", incident_new: "reopened", muted: "muted", unmuted: "unmuted",
  memory_on: "put in the team's memory", memory_off: "took out of the team's memory",
  memory_note: "added to the team's memory", memory_fresh_days: "set memory freshness to (days)",
  seat_limit: "set the seat limit to", seat_limit_removed: "removed the seat limit",
  agent_family: "kept on the free plan", key_mint: "minted the key", key_revoke: "revoked the key",
  key_change: "changed what this key may do:",
  permission_granted: "let", permission_withdrawn: "no longer lets",
};

function detailWords(it: AuditItem): string {
  const d = it.detail ?? {};
  const parts: string[] = [];
  if (typeof d.calls === "number") parts.push(`${d.calls} calls`);
  if (typeof d.reason === "string") parts.push(`“${d.reason}”`);
  if (typeof d.note === "string") parts.push(`“${d.note}”`);
  if (typeof d.text === "string") parts.push(`“${d.text}”`);
  if (typeof d.asked_by === "string") parts.push(`asked by ${d.asked_by}`);
  if (typeof d.to === "string") parts.push(`→ ${d.to}`);
  if (Array.isArray(d.invited) && d.invited.length) parts.push(`invited ${d.invited.join(", ")}`);
  if (typeof d.repo === "string") parts.push(`in ${d.repo}`);
  if (Array.isArray(d.scopes)) parts.push(d.scopes.join(", "));
  if (d.guest) parts.push("a guest");
  return parts.join(" · ");
}

function save(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function AuditTrail() {
  const [action, setAction] = useState("");
  const [who, setWho] = useState("");
  const [target, setTarget] = useState("");
  const [since, setSince] = useState("");
  const [exportError, setExportError] = useState<string | null>(null);
  const q = useQuery({
    queryKey: ["audit", action, who, target, since],
    queryFn: () => api.audit({ action, who, target, since, limit: 200 }),
    retry: false,
  });
  // node ids read as the session's task where there is one
  const nodes = useQuery({ queryKey: ["nodes"], queryFn: api.nodes });
  const titleOf = new Map((nodes.data ?? []).filter((n) => n.profile?.title)
    .map((n) => [n.node_id, n.profile!.title!]));
  const field = { background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: C.text,
                  fontFamily: MONO, fontSize: 11, padding: "3px 7px", outline: "none" } as const;
  const download = async (format: "csv" | "jsonl") => {
    setExportError(null);
    try {
      const { blob, name } = await api.auditExport(format, since || undefined);
      save(blob, name);
    } catch (e) {
      setExportError(errorWords((e as Error).message));
    }
  };
  if (q.isError) {
    return (
      <div data-testid="audit-trail" style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut }}>
        {errorWords((q.error as Error).message)} — the audit trail is for the workspace&apos;s admins.
      </div>
    );
  }
  return (
    <div data-testid="audit-trail" style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut }}>
      <div style={{ color: C.dim, fontSize: 10.5, lineHeight: 1.6, marginBottom: 10 }}>
        Every control acted on in this workspace — who paused, stopped or capped which agent, the team&apos;s
        brake, the rules and their exceptions, skills, the seat limit, pairs and notes, keys — newest first.
        Kept under the deployment&apos;s retention, never trimmed by how much happens.
      </div>
      <div className="flex gap-2 flex-wrap items-center" style={{ marginBottom: 10 }}>
        <select value={action} onChange={(e) => setAction(e.target.value)} aria-label="action" style={field}
          data-testid="audit-action">
          <option value="">every act</option>
          {(q.data?.actions ?? []).map((a) => <option key={a} value={a}>{WORDS[a] ?? a}</option>)}
        </select>
        <input value={who} onChange={(e) => setWho(e.target.value)} placeholder="who" aria-label="who"
          style={{ ...field, width: 130 }} data-testid="audit-who" />
        <input value={target} onChange={(e) => setTarget(e.target.value)} placeholder="agent, rule, key…"
          aria-label="target" style={{ ...field, width: 140 }} />
        <input type="date" value={since} onChange={(e) => setSince(e.target.value)} aria-label="since"
          style={field} />
        <span style={{ flex: 1 }} />
        <button onClick={() => void download("csv")} data-testid="audit-export-csv"
          style={btn({ color: C.text, borderColor: C.steel, fontSize: 11, padding: "3px 10px" })}>
          export CSV
        </button>
        <button onClick={() => void download("jsonl")} data-testid="audit-export-jsonl"
          style={btn({ color: C.mut, fontSize: 11, padding: "3px 10px" })}>
          JSON lines
        </button>
      </div>
      {exportError && <div style={{ color: C.red, fontSize: 11 }}>{exportError}</div>}
      {q.data?.window_days && (
        <div data-testid="audit-window" style={{ color: C.dim, fontSize: 10.5, marginBottom: 6 }}>
          the last {q.data.window_days} days — the whole trail and its export are on the{" "}
          <a href="#/pricing?for=coding" style={{ color: C.steel }}>Business plan</a>
        </div>
      )}
      <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
        {(q.data?.items ?? []).length === 0 && (
          <div className="p-3" style={{ color: C.dim }}>
            {q.isLoading ? "loading…" : "nothing on the trail yet — controls land here as they are used."}
          </div>
        )}
        {(q.data?.items ?? []).map((it, i) => (
          <div key={it.id} data-testid="audit-row" className="px-3 py-1.5 flex gap-2 flex-wrap"
            style={{ borderTop: i === 0 ? "none" : `1px solid ${C.line}`, fontSize: 11 }}>
            <span style={{ color: C.dim, width: 128 }}>{it.ts.slice(0, 16).replace("T", " ")}</span>
            <span style={{ color: C.text }}>{it.who || "—"}</span>
            <span>{WORDS[it.action] ?? it.action}</span>
            {it.target && (
              <span style={{ color: C.steel }} title={it.target}>{titleOf.get(it.target) ?? it.target}</span>
            )}
            {detailWords(it) && <span style={{ color: C.dim }}>{detailWords(it)}</span>}
          </div>
        ))}
      </div>
      {q.data && q.data.total > q.data.items.length && (
        <div style={{ color: C.dim, fontSize: 10.5, marginTop: 6 }}>
          showing {q.data.items.length} of {q.data.total} — the export has them all
        </div>
      )}
    </div>
  );
}
