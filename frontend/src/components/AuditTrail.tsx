// The workspace's audit trail: who paused, stopped or capped which agent, who
// pulled the team's brake, granted an exception, changed the rules, the seat
// limit, a skill — and the keys minted and revoked. Filtered here, exported as
// CSV or JSON lines for an archive. Admins only (the plane says so with 403).
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type AuditItem, type Hold } from "../api";
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
  audit_retention: "set how long the audit trail is kept:",
  member_added: "added to the workspace", member_role: "changed the role of", member_removed: "removed from the workspace",
  invite_sent: "invited", invite_withdrawn: "withdrew the invitation",
  sso_set: "set up single sign-on with", sso_removed: "turned single sign-on off",
  scim_token_made: "made a SCIM token", scim_token_revoked: "turned SCIM provisioning off",
  scim_group_role: "mapped the provider's group", scim_added: "added", scim_removed: "removed",
  scim_role: "changed the role of", scim_renamed: "changed the address of",
  sessions_limit: "set how long a sign-in lasts:", sessions_ended: "signed out",
  plan_activated: "activated the plan",
  hold_placed: "put a legal hold on the trail", hold_released: "lifted the legal hold",
};

function detailWords(it: AuditItem): string {
  const d = it.detail ?? {};
  const parts: string[] = [];
  if (typeof d.calls === "number") parts.push(`${d.calls} calls`);
  if (typeof d.reason === "string") parts.push(`“${d.reason}”`);
  if (typeof d.note === "string") parts.push(`“${d.note}”`);
  if (typeof d.text === "string") parts.push(`“${d.text}”`);
  if (typeof d.asked_by === "string") parts.push(`asked by ${d.asked_by}`);
  if (typeof d.to === "string") parts.push(typeof d.from === "string" ? `${d.from} → ${d.to}` : `→ ${d.to}`);
  if (Array.isArray(d.invited) && d.invited.length) parts.push(`invited ${d.invited.join(", ")}`);
  if (d.invited === true) parts.push("by invitation");
  if (typeof d.repo === "string") parts.push(`in ${d.repo}`);
  if (typeof d.role === "string") parts.push(`as ${d.role}`);
  if (typeof d.protocol === "string") parts.push(d.protocol === "saml" ? "SAML" : "OpenID Connect");
  if (Array.isArray(d.domains) && d.domains.length) parts.push(d.domains.join(", "));
  if (d.enforce === true) parts.push("the only way in");
  if (typeof d.via === "string") parts.push(`through ${d.via}`);
  if (typeof d.until === "string") parts.push(`until ${d.until}`);
  if (d.by_group === true) parts.push("by the provider's groups");
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

/** Business: how long the trail is kept, and a month of it as a reviewer
 *  reads it (who acted on whose agent, every exception, permission, brake). */
function KeepAndReport({ field }: { field: React.CSSProperties }) {
  const qc = useQueryClient();
  const keep = useQuery({ queryKey: ["audit-retention"], queryFn: api.auditRetention, retry: false });
  const setKeep = useMutation({
    mutationFn: (days: number | null) => api.setAuditRetention(days),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["audit-retention"] });
      void qc.invalidateQueries({ queryKey: ["audit"] });
    },
  });
  const [month, setMonth] = useState(new Date().toISOString().slice(0, 7));
  const report = useQuery({ queryKey: ["audit-report", month], queryFn: () => api.auditReport(month),
                            retry: false });
  const r = report.data;
  return (
    <div style={{ marginBottom: 10 }}>
      <div className="flex gap-2 items-center flex-wrap" data-testid="audit-retention">
        <span>keep the trail</span>
        <select value={keep.data?.days ?? ""} aria-label="keep the trail" style={field}
          disabled={setKeep.isPending}
          onChange={(e) => setKeep.mutate(e.target.value ? Number(e.target.value) : null)}>
          <option value="">as long as the deployment keeps it</option>
          {[90, 180, 365, 730, 2555].map((d) => <option key={d} value={d}>{d >= 365 ? `${Math.round(d / 365)} year${d >= 730 ? "s" : ""}` : `${d} days`}</option>)}
        </select>
        {setKeep.data && setKeep.data.pruned > 0 && (
          <span style={{ color: C.dim }}>{setKeep.data.pruned} older act{setKeep.data.pruned === 1 ? "" : "s"} dropped</span>
        )}
      </div>
      <div className="flex gap-2 items-center flex-wrap mt-2" data-testid="audit-report">
        <span>monthly report</span>
        <input type="month" value={month} onChange={(e) => setMonth(e.target.value)} aria-label="report month" style={field} />
        {r && (
          <>
            <span style={{ color: C.text }}>{r.acts} act{r.acts === 1 ? "" : "s"}</span>
            <span>· {Object.keys(r.by_person).length} {Object.keys(r.by_person).length === 1 ? "person" : "people"}</span>
            <span style={{ color: r.on_others_sessions.length ? C.amber : C.dim }}>
              · {r.on_others_sessions.length} on someone else&apos;s agent
            </span>
            <span>· {r.policy_changes.length} policy, {r.permission_changes.length} permission change{r.permission_changes.length === 1 ? "" : "s"}, {r.brakes.length} brake{r.brakes.length === 1 ? "" : "s"}</span>
            <span data-testid="audit-report-access">· {(r.access_changes ?? []).length} change{(r.access_changes ?? []).length === 1 ? "" : "s"} to people and sign-in</span>
            <button data-testid="audit-report-save"
              onClick={() => save(new Blob([JSON.stringify(r, null, 2)], { type: "application/json" }), `axor-audit-report-${r.month}.json`)}
              style={btn({ color: C.mut, fontSize: 11, padding: "2px 8px" })}>
              save the report
            </button>
          </>
        )}
      </div>
    </div>
  );
}

/** Enterprise: a period of the whole governed surface as one document a
 *  reviewer can read and check, and a hold that stops this workspace's own
 *  retention from deleting the evidence under it. */
function ForTheReviewer({ field }: { field: React.CSSProperties }) {
  const qc = useQueryClient();
  const holds = useQuery({ queryKey: ["audit-holds"], queryFn: api.holds, retry: false });
  const place = useMutation({
    mutationFn: (reason: string) => api.placeHold(reason),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["audit-holds"] });
                       void qc.invalidateQueries({ queryKey: ["audit"] }); },
  });
  const release = useMutation({
    mutationFn: (id: string) => api.releaseHold(id),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["audit-holds"] });
                       void qc.invalidateQueries({ queryKey: ["audit"] }); },
  });
  const quarter = (() => {
    const now = new Date();
    const q = Math.floor(now.getUTCMonth() / 3);
    const start = q === 0 ? new Date(Date.UTC(now.getUTCFullYear() - 1, 9, 1))
                          : new Date(Date.UTC(now.getUTCFullYear(), (q - 1) * 3, 1));
    const end = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth() + 3, 1));
    return { from: start.toISOString().slice(0, 10), to: end.toISOString().slice(0, 10) };
  })();
  const [from, setFrom] = useState(quarter.from);
  const [to, setTo] = useState(quarter.to);
  const [error, setError] = useState<string | null>(null);
  // the pack is Enterprise's: below it the holds call says so, and this whole
  // section stays out of the way
  if (holds.isError) return null;
  const get = async (format: "json" | "html") => {
    setError(null);
    try {
      const { blob, name } = await api.compliancePack(from, to, format);
      if (format === "html") window.open(URL.createObjectURL(blob), "_blank");
      else save(blob, name);
    } catch (e) { setError(errorWords((e as Error).message)); }
  };
  const open = (holds.data?.holds ?? []).filter((h: Hold) => !h.released_ts);
  return (
    <div data-testid="audit-reviewer" style={{ marginBottom: 10 }}>
      <div className="flex gap-2 items-center flex-wrap">
        <span>compliance pack</span>
        <input type="date" value={from} onChange={(e) => setFrom(e.target.value)}
          aria-label="period from" style={field} data-testid="pack-from" />
        <input type="date" value={to} onChange={(e) => setTo(e.target.value)}
          aria-label="period to" style={field} data-testid="pack-to" />
        <button data-testid="pack-read" onClick={() => void get("html")}
          style={btn({ color: C.text, borderColor: C.steel, fontSize: 11, padding: "3px 10px" })}>
          read it
        </button>
        <button data-testid="pack-json" onClick={() => void get("json")}
          style={btn({ color: C.mut, fontSize: 11, padding: "3px 10px" })}>
          save the JSON
        </button>
      </div>
      <div style={{ color: C.dim, fontSize: 10.5, lineHeight: 1.6, marginTop: 2 }}>
        one document for a reviewer: who oversaw which agent and why, every attestation,
        policy and access change, and what each answers — with a digest over the body.
      </div>
      <div className="flex gap-2 items-center flex-wrap mt-2" data-testid="audit-holds">
        <span>legal hold</span>
        {open.length === 0 ? (
          <>
            <span style={{ color: C.dim }}>none — retention prunes on its schedule</span>
            <button data-testid="hold-place" disabled={place.isPending}
              onClick={() => { const r = window.prompt("What is the hold for? (a case, an investigation)");
                               if (r && r.trim().length > 2) place.mutate(r.trim()); }}
              style={btn({ color: C.amber, fontSize: 11, padding: "2px 8px" })}>
              put one on
            </button>
          </>
        ) : open.map((h: Hold) => (
          <span key={h.hold_id} className="flex gap-2 items-center">
            <span style={{ color: C.amber }}>● {h.reason}</span>
            <span style={{ color: C.dim }}>since {h.placed_ts.slice(0, 10)} · nothing is pruned</span>
            <button data-testid={`hold-release-${h.hold_id}`} disabled={release.isPending}
              onClick={() => { if (window.confirm("Lift the hold? Retention starts pruning again.")) release.mutate(h.hold_id); }}
              style={btn({ color: C.mut, fontSize: 11, padding: "2px 8px" })}>
              lift it
            </button>
          </span>
        ))}
      </div>
      {(error || place.isError || release.isError) && (
        <div style={{ color: C.red, fontSize: 11 }}>
          {error ?? errorWords(((place.error ?? release.error) as Error).message)}
        </div>
      )}
    </div>
  );
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
      {q.data && !q.data.window_days && <KeepAndReport field={field} />}
      {q.data && !q.data.window_days && <ForTheReviewer field={field} />}
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
