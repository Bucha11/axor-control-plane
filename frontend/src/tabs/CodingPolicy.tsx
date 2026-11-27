// The team's rules for coding agents (Claude Code, Codex): what the hooks
// refuse on every developer's machine, whoever asked — decided once here by an
// admin. A repository may refuse more (.axor/rules.json "refuse"), never allow
// what the team refuses. Until an admin saves, each repository decides.
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, CodingException, CodingRules } from "../api";
import { C, MONO, btn } from "../theme";
import { errorWords } from "../refusal";
import { useCanAdmin, useMay } from "../team";
import { localTime } from "../time";

export default function CodingPolicy() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["coding-rules"], queryFn: api.codingRules });
  const canEdit = useCanAdmin();
  // exceptions and trusted sources: whoever the workspace lets grant them
  const may = useMay();
  const canGrant = canEdit || may("exceptions");
  const [allow, setAllow] = useState<Set<string>>(new Set());
  const [mode, setMode] = useState<"enforce" | "observe">("enforce");
  useEffect(() => {
    if (q.data) { setAllow(new Set(q.data.allow)); setMode(q.data.mode); }
  }, [q.data]);
  const save = useMutation({
    mutationFn: () => api.setCodingRules([...allow], mode, {}, q.data?.version),
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
      {d && canEdit && <PolicySignature signed={d.signed} />}
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
        {d?.set && <span style={{ color: C.dim }}>the team's policy · by {d.by || "—"} · {localTime(d.ts)}</span>}
        {save.isSuccess && <span style={{ color: C.green }}>saved — every sidecar picks it up within a minute</span>}
        {save.isError && <SaveError error={save.error} reset={save.reset} />}
      </div>
      {d && <Exceptions d={d} canEdit={canGrant} />}
      {d && <TrustedSources d={d} canEdit={canGrant} />}
    </div>
  );
}

const field: React.CSSProperties = {
  background: C.panel2, border: `1px solid ${C.line}`, borderRadius: 5, color: C.text,
  fontFamily: MONO, fontSize: 11.5, padding: "4px 8px",
};
const head: React.CSSProperties = { fontSize: 15, fontWeight: 650, margin: "28px 0 4px" };
const sub: React.CSSProperties = { fontSize: 13, color: C.mut, lineHeight: 1.6, marginBottom: 10 };

/** Save the exceptions or the trusted sources as they are now, with the
 *  team's rules and mode as SAVED (not this screen's unsaved toggles). */
function useSaveMore() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ d, more }: { d: CodingRules; more: { exceptions?: CodingException[]; trusted_sources?: string[] } }) =>
      api.setCodingExceptions(more, d.version),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["coding-rules"] }),
  });
}

function Exceptions({ d, canEdit }: { d: CodingRules; canEdit: boolean }) {
  const save = useSaveMore();
  const rules = (d.catalog ?? []).filter((r) => !r.fixed);
  const [rule, setRule] = useState(rules[0]?.id ?? "");
  const [kind, setKind] = useState<"path" | "command">("path");
  const [value, setValue] = useState("");
  const [note, setNote] = useState("");
  const what = (id: string) => d.catalog?.find((r) => r.id === id)?.what ?? id;
  const everything = value.replace(/[*/\s]/g, "") === "";
  return (
    <div data-testid="policy-exceptions">
      <h2 style={head}>Exceptions</h2>
      <div style={sub}>
        One thing let through a rule, for the team — a path (<code>tests/fixtures/*</code>) or a whole
        command (<code>env | grep JWT</code>), <code>*</code> as a wildcard. The rule keeps refusing everything else.
      </div>
      {d.exceptions.length > 0 && (
        <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8, marginBottom: 10 }}>
          {d.exceptions.map((e, i) => (
            <div key={i} data-testid="policy-exception" className="flex items-center gap-3 px-4 py-2 flex-wrap"
              style={{ borderTop: i ? `1px solid ${C.line}` : "none", fontFamily: MONO, fontSize: 11.5 }}>
              <span style={{ color: C.dim, minWidth: 150 }}>{what(e.rule)}</span>
              <span style={{ color: C.text, flex: 1, overflowWrap: "anywhere" }}>
                {e.path ? "path " : "command "}<code>{e.path ?? e.command}</code>
                {e.note && <span style={{ color: C.dim }}> — {e.note}</span>}
              </span>
              <span style={{ color: C.dim, fontSize: 10.5 }}>{e.by}</span>
              {canEdit && (
                <button data-testid="policy-exception-remove" disabled={save.isPending}
                  onClick={() => save.mutate({ d, more: { exceptions: d.exceptions.filter((_, j) => j !== i) } })}
                  style={btn({ fontSize: 10.5, padding: "2px 8px" })}>remove</button>
              )}
            </div>
          ))}
        </div>
      )}
      {canEdit && (
        <div className="flex items-center gap-2 flex-wrap" style={{ fontFamily: MONO, fontSize: 11.5 }}>
          <select aria-label="rule" value={rule} onChange={(e) => setRule(e.target.value)} style={field}>
            {rules.map((r) => <option key={r.id} value={r.id}>{r.what}</option>)}
          </select>
          <select aria-label="exception kind" value={kind} onChange={(e) => setKind(e.target.value as "path" | "command")} style={field}>
            <option value="path">path</option>
            <option value="command">command</option>
          </select>
          <input data-testid="policy-exception-value" value={value} onChange={(e) => setValue(e.target.value)}
            placeholder={kind === "path" ? "tests/fixtures/*" : "env | grep JWT"} style={{ ...field, flex: 1, minWidth: 180 }} />
          <input aria-label="why" value={note} onChange={(e) => setNote(e.target.value)} placeholder="why (optional)"
            style={{ ...field, width: 160 }} />
          <button data-testid="policy-exception-add" disabled={!value.trim() || everything || save.isPending}
            onClick={() => save.mutate({ d, more: { exceptions: [...d.exceptions, { rule, [kind]: value.trim(), note }] } },
              { onSuccess: () => { setValue(""); setNote(""); } })}
            style={btn({ fontSize: 11, padding: "4px 12px" })}>add</button>
        </div>
      )}
      {canEdit && !d.set && (
        <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 6 }}>
          adding one saves the team's policy as shown above
        </div>
      )}
      {save.isError && <div style={{ fontFamily: MONO, fontSize: 11, marginTop: 6 }}><SaveError error={save.error} reset={save.reset} /></div>}
    </div>
  );
}

function TrustedSources({ d, canEdit }: { d: CodingRules; canEdit: boolean }) {
  const save = useSaveMore();
  const [host, setHost] = useState("");
  const clean = host.trim().toLowerCase().replace(/^https?:\/\//, "").replace(/\/.*$/, "");
  return (
    <div data-testid="policy-trusted-sources">
      <h2 style={head}>Trusted sources</h2>
      <div style={sub}>
        Hosts whose pages the agents read like your developers' own words: a command such a page shows
        (<code>pip install pyjwt</code> on the package's docs) can be run. Name documentation you trust —
        never a site anyone can write to (a code host's issues, a forum, a pastebin, a blog platform, or
        every site under <code>*.github.io</code> or <code>*.co.uk</code>): an instruction planted there
        would run. <code>*.readthedocs.io</code> covers its subdomains.
      </div>
      {d.trusted_sources.length > 0 && (
        <div className="flex gap-2 flex-wrap mb-3">
          {d.trusted_sources.map((h) => (
            <span key={h} data-testid="trusted-source" className="flex items-center gap-2"
              style={{ fontFamily: MONO, fontSize: 11.5, color: C.text, border: `1px solid ${C.line}`,
                       borderRadius: 14, padding: "3px 6px 3px 10px" }}>
              {h}
              {canEdit && (
                <button aria-label={`stop trusting ${h}`} disabled={save.isPending}
                  onClick={() => save.mutate({ d, more: { trusted_sources: d.trusted_sources.filter((x) => x !== h) } })}
                  style={{ background: "none", border: "none", color: C.dim, cursor: "pointer", fontFamily: MONO }}>×</button>
              )}
            </span>
          ))}
        </div>
      )}
      {canEdit && (
        <div className="flex items-center gap-2 flex-wrap" style={{ fontFamily: MONO, fontSize: 11.5 }}>
          <input data-testid="trusted-source-host" value={host} onChange={(e) => setHost(e.target.value)}
            placeholder="pypi.org or *.readthedocs.io" style={{ ...field, width: 260 }} />
          <button data-testid="trusted-source-add" disabled={!clean || save.isPending}
            onClick={() => save.mutate({ d, more: { trusted_sources: [...d.trusted_sources, clean] } },
              { onSuccess: () => setHost("") })}
            style={btn({ fontSize: 11, padding: "4px 12px" })}>trust</button>
        </div>
      )}
      {save.isError && <div style={{ fontFamily: MONO, fontSize: 11, marginTop: 6 }}><SaveError error={save.error} reset={save.reset} /></div>}
    </div>
  );
}

/** A save refused because someone saved the policy since this screen read
 *  it (QA-212) says so, with a way to see theirs — never "saved". */
function SaveError({ error, reset }: { error: unknown; reset: () => void }) {
  const qc = useQueryClient();
  const stale = error instanceof ApiError && error.status === 409 && error.code === "stale";
  return (
    <span data-testid={stale ? "policy-stale" : undefined} style={{ color: stale ? C.amber : C.red }}>
      {errorWords((error as Error).message)}
      {stale && (
        <button data-testid="policy-reload" onClick={() => { reset(); void qc.invalidateQueries({ queryKey: ["coding-rules"] }); }}
          style={btn({ fontSize: 10.5, padding: "2px 8px", marginLeft: 8 })}>reload</button>
      )}
    </span>
  );
}


/** Sidecars connected with operator keys take what widens — these rules, the
 *  exceptions and trusted sources, the skills turned on for the team — only
 *  from a policy an operator signed. Says whether what stands here is signed. */
function PolicySignature({ signed }: { signed?: CodingRules["signed"] }) {
  const qc = useQueryClient();
  const sign = useMutation({
    mutationFn: api.signCodingPolicy,
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["coding-rules"] }),
  });
  const armed = api.canSign();
  const current = !!signed?.current;
  return (
    <div data-testid="policy-signature" className="mb-4 p-3 flex items-center gap-3 flex-wrap"
      style={{ border: `1px solid ${current ? C.line : C.amber}`, borderRadius: 8,
               fontFamily: MONO, fontSize: 11.5, color: C.mut, lineHeight: 1.6 }}>
      <span style={{ flex: 1, minWidth: 260 }}>
        {current
          ? `signed — sidecars with operator keys apply this policy (v${signed!.seq})`
          : signed?.seq
            ? `changed since it was signed (v${signed.seq}) — sidecars with operator keys keep the signed one until you sign this`
            : "not signed — sidecars with operator keys apply none of what this policy allows, and install no team skill"}
      </span>
      {!current && (
        armed
          ? <button data-testid="policy-sign" onClick={() => sign.mutate()} disabled={sign.isPending}
              style={btn({ fontSize: 11, padding: "4px 12px", color: C.text })}>sign the team's policy</button>
          : <span style={{ color: C.dim }}>make an operator key in this browser (Settings → workspace → operator keys) to sign it</span>
      )}
      {sign.error && <span style={{ color: C.red }}>{(sign.error as Error).message}</span>}
    </div>
  );
}
