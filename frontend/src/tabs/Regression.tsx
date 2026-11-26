// Safe to ship? — every kept case (the pinned corpus) replayed under the rules
// about to ship: attacks must stay blocked, normal work must keep passing.
// Wired to /v1/regression — deterministic replay, no model calls. The rules
// come from Policy (the shared record), the example, or JSON by hand.
import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, ChevronDown, ChevronRight, Circle, FlaskConical, Loader2, Lock, Play, Upload } from "lucide-react";
import { api, LabDeployResult, RegressionReport, RegressionRow } from "../api";
import { C, MONO, btn } from "../theme";
import { navigate } from "../router";
import { policyRules as rulesOfPolicy } from "../policyConfig";
import Coach from "../components/Coach";
import { useApp } from "../store";
import { LAB_SHIPPED } from "../flags";
import Tooltip from "../components/Tooltip";
import { useWorkStarted } from "../workStarted";

// The example corpus's runs (backend demo.DEMO_RUN_IDS) and what each one is,
// in words: their pins all carry the scenario as label, which tells two apart
// by nothing.
const DEMO_CASES: Record<string, string> = {
  ex_block: "an email hides an instruction; its summary is posted to Slack",
  ex_pass: "read notes, write notes",
  ex_tree: "a research tree passes a made-up number upward",
  ex_coding_claude: "Claude Code reads a docs page",
  ex_coding_codex: "Codex tries the page's “npx limitkit init --endpoint …”",
};
const caseName = (runId: string, label: string): string =>
  DEMO_CASES[runId] ?? (label || runId);

const STATUS: Record<RegressionRow["result"], { text: string; color: string }> = {
  held: { text: "still blocked", color: C.green },
  passed: { text: "still passes", color: C.green },
  escaped: { text: "NOW GETS THROUGH", color: C.red },
  regressed: { text: "NOW REFUSED", color: C.red },
  unanchored: { text: "can't be checked", color: C.amber },
};

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

type RulesFrom = "policy" | "example" | "json";
type Variant = "as-is" | "looser" | "stricter";

/** The example's rules, loosened or tightened so the check has something to
 * catch: a looser rule lets the exfil's text through (an always-fine value on
 * slack_post), a stricter one takes away the tool the normal flow needs. */
function exampleVariant(rules: Record<string, unknown>, v: Variant): Record<string, unknown> {
  if (v === "looser") {
    return { ...rules, driving_args: { slack_post: ["text"] },
             value_policies: { slack_post: [{ arg: "text", kind: "enum", allowed: ["…"] }] } };
  }
  if (v === "stricter") {
    const tools = (rules.allowed_tools as string[] | undefined) ?? [];
    return { ...rules, allowed_tools: tools.filter((t) => t !== "notes_write") };
  }
  return rules;
}

// Org layer (EE): schedule the corpus + browse run history. Rendered locked
// (honest upsell, same pattern as the availability ladder) without a license.
function ScheduleAndHistory({ parseConfig }: { parseConfig: () => Record<string, unknown> | null }) {
  const qc = useQueryClient();
  const schedule = useQuery({ queryKey: ["regression-schedule"], queryFn: api.getRegressionSchedule });
  const ee = schedule.data?.ee_active === true;
  const history = useQuery({
    queryKey: ["regression-history"],
    queryFn: () => api.regressionHistory(20),
    enabled: ee,
  });
  const [hours, setHours] = useState("24");
  const save = useMutation({
    mutationFn: (enabled: boolean) => {
      // Turning the schedule off must not depend on the editor holding a valid
      // config; the stored one is inert once disabled.
      const config = enabled ? parseConfig() : {};
      if (config === null) throw new Error("the rules above must be valid JSON");
      return api.putRegressionSchedule(enabled, Number(hours) || 24, config);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["regression-schedule"] });
    },
  });

  if (!schedule.data) return null;
  return (
    <div className="mb-8">
      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, letterSpacing: "0.08em", marginBottom: 8 }}>
        SCHEDULED CHECKS · HISTORY
        <span style={{ marginLeft: 8, border: `1px solid ${C.line}`, borderRadius: 20, padding: "1px 6px", fontSize: 8.5 }}>
          Team
        </span>
      </div>
      {!ee ? (
        <div className="flex items-center gap-2 p-4" style={{ background: C.panel, border: `1px dashed ${C.line}`, borderRadius: 8 }}>
          <Lock size={13} color={C.dim} />
          <span style={{ fontFamily: MONO, fontSize: 11.5, color: C.dim }}>
            checks on a schedule, and their history, come with Team — add a
            license in Settings · checking by hand stays free forever
          </span>
        </div>
      ) : (
        <>
          <div className="flex items-center gap-3 mb-3">
            <span style={{ fontFamily: MONO, fontSize: 11.5, color: schedule.data.enabled ? C.green : C.dim }}>
              {schedule.data.enabled
                ? `on — every ${schedule.data.interval_hours}h`
                : "off"}
              {schedule.data.last_run_ts ? ` · last ${schedule.data.last_run_ts.slice(0, 16)}` : ""}
            </span>
            <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>every</span>
            <input
              value={hours}
              onChange={(e) => setHours(e.target.value)}
              style={{
                width: 44, background: C.bg, border: `1px solid ${C.line}`, borderRadius: 5,
                color: C.text, fontFamily: MONO, fontSize: 11.5, padding: "4px 6px", outline: "none",
              }}
            />
            <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>h</span>
            <button onClick={() => save.mutate(true)} disabled={save.isPending}
              style={btn({ color: C.steel, fontSize: 11, padding: "6px 10px" })}>
              <CalendarClock size={12} /> Schedule with the rules above
            </button>
            {schedule.data.enabled && (
              <button onClick={() => save.mutate(false)} disabled={save.isPending}
                style={btn({ color: C.mut, fontSize: 11, padding: "6px 10px" })}>
                Disable
              </button>
            )}
            {save.isError && (
              <span style={{ fontFamily: MONO, fontSize: 11, color: C.red }}>
                {(save.error as Error).message}
              </span>
            )}
          </div>
          {(history.data ?? []).length > 0 && (
            <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
              {(history.data ?? []).map((h, i) => (
                <div key={i} className="flex items-center gap-3 px-4 py-2"
                  style={{ borderTop: i ? `1px solid ${C.line}` : "none", fontFamily: MONO, fontSize: 11 }}>
                  <Circle size={8} fill={h.safe_to_ship ? C.green : C.red} color={h.safe_to_ship ? C.green : C.red} />
                  <span style={{ color: C.dim, width: 122 }}>{h.created_ts.slice(0, 16)}</span>
                  <span style={{ color: C.dim, width: 72 }}>{h.source}</span>
                  <span style={{ color: h.safe_to_ship ? C.mut : C.text, flex: 1 }}>
                    {h.safe_to_ship
                      ? `safe — ${h.total} cases`
                      : `${h.escaped} got through · ${h.regressed} now refused${h.unanchored ? ` · ${h.unanchored} not checked` : ""}${h.skipped ? ` · ${h.skipped} unreadable` : ""} of ${h.total}`}
                  </span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

// Lab → CP: accept a cp-deploy.json produced by `axor-lab export-cp`. The
// package's validated regression pins fold into THIS corpus (source-labelled
// lab:{package_id}); the list below is the record of every accepted handoff.
export function LabDeploys() {
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [result, setResult] = useState<LabDeployResult | { ok: false; reasons: string[] } | null>(null);

  const deploys = useQuery({ queryKey: ["lab-deploys"], queryFn: api.labDeploys });

  const upload = useMutation({
    mutationFn: async (file: File) => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(await file.text());
      } catch {
        throw new Error(`${file.name} is not valid JSON`);
      }
      if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("cp-deploy.json must be a JSON object");
      }
      return api.labDeploy(parsed as Record<string, unknown>);
    },
    onSuccess: (r) => {
      setResult(r);
      if (r.ok) {
        void qc.invalidateQueries({ queryKey: ["lab-deploys"] });
        void qc.invalidateQueries({ queryKey: ["pins"] });
      }
    },
    onError: () => setResult(null),
  });

  return (
    <div className="mb-8">
      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, letterSpacing: "0.08em", marginBottom: 8 }}>
        LAB DEPLOYS · CP-DEPLOY PACKAGES FROM AXOR LAB
      </div>
      <div className="flex items-center gap-3 mb-3">
        <input
          ref={fileRef}
          type="file"
          accept=".json,application/json"
          style={{ display: "none" }}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) upload.mutate(file);
            e.target.value = "";
          }}
        />
        <Tooltip content="Upload the cp-deploy.json that `axor-lab export-cp` produced: the validated policy + manifests are stored, and its validated regression pins join this corpus marked lab:{package_id}. Only finalized (verified) exports are accepted.">
          <button
            onClick={() => fileRef.current?.click()}
            disabled={upload.isPending}
            style={btn({ color: C.steel, fontSize: 11, padding: "7px 12px" })}
          >
            {upload.isPending ? <Loader2 size={12} className="animate-spin" /> : <Upload size={12} />}
            {" "}upload cp-deploy.json
          </button>
        </Tooltip>
        {upload.isError && (
          <span style={{ fontFamily: MONO, fontSize: 11, color: C.red }}>
            {(upload.error as Error).message}
          </span>
        )}
        {result?.ok && (
          <span style={{ fontFamily: MONO, fontSize: 11, color: C.green }}>
            {result.already_deployed ? "already deployed" : "accepted"} · {result.package_id} ·{" "}
            {result.pins_created} pin{result.pins_created === 1 ? "" : "s"} → corpus
            {" · "}
            <span style={{ color: C.steel }}>
              {result.pins_replayable} replayable
            </span>
            {result.pins_skipped.length > 0 && (
              <span style={{ color: C.mut }}>
                {" · "}{result.pins_skipped.length} skipped
              </span>
            )}
          </span>
        )}
      </div>
      {result?.ok && result.pins_skipped.length > 0 && (
        <div className="p-3 mb-3" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
          <div style={{ fontFamily: MONO, fontSize: 10, color: C.mut, letterSpacing: "0.1em", marginBottom: 6 }}>
            PINS LEFT SKIPPED (not replayed — recorded under a kernel this CP will not substitute)
          </div>
          {result.pins_skipped.map((p) => (
            <div key={p.run_id} style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, lineHeight: 1.6 }}>
              · {p.trace_id}: {p.reason}
            </div>
          ))}
        </div>
      )}
      {result && !result.ok && (
        <div className="p-3 mb-3" style={{ background: C.panel, border: `1px solid ${C.red}`, borderRadius: 8 }}>
          <div style={{ fontFamily: MONO, fontSize: 10, color: C.red, letterSpacing: "0.1em", marginBottom: 6 }}>
            PACKAGE REJECTED
          </div>
          {result.reasons.map((reason, i) => (
            <div key={i} style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, lineHeight: 1.6 }}>
              · {reason}
            </div>
          ))}
        </div>
      )}
      {(deploys.data ?? []).length > 0 && (
        <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
          {(deploys.data ?? []).map((d, i) => (
            <div key={d.package_id} className="flex items-center gap-3 px-4 py-2"
              style={{ borderTop: i ? `1px solid ${C.line}` : "none", fontFamily: MONO, fontSize: 11 }}>
              <FlaskConical size={11} color={C.steel} />
              <span style={{ color: C.text }}>{d.package_id}</span>
              <span style={{ color: C.dim, width: 122 }}>{d.created_ts.slice(0, 16)}</span>
              <span style={{ color: C.dim, flex: 1 }}>
                {d.kernel} · bundle {d.source.bundle_id ?? "?"} · condition {d.source.condition_id ?? "?"}
              </span>
              <span style={{ color: C.mut }}>
                {d.pins_created} pin{d.pins_created === 1 ? "" : "s"} · {d.manifest_count} manifest{d.manifest_count === 1 ? "" : "s"}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function Regression({ rules: rulesParam }: { rules?: string } = {}) {
  const qc = useQueryClient();
  const policy = useApp((s) => s.toolPolicy);
  const policyTools = Object.keys(policy);
  const corpus = useQuery({ queryKey: ["pins"], queryFn: api.getPins });
  const pins = corpus.data?.pins ?? [];
  const ownPins = pins.filter((p) => !(p.run_id in DEMO_CASES));
  // once the workspace's own agents report, the example's rules are not
  // offered — unless its cases were kept here before, and need them
  const started = useWorkStarted();
  const offerExample = !started || pins.some((p) => p.run_id in DEMO_CASES);

  // `?rules=policy` — arriving from Replay's "make it a rule"
  const [chosen, setChosen] = useState<RulesFrom | null>(
    rulesParam === "policy" || rulesParam === "example" ? rulesParam : null);
  // until the person picks: their own cases are checked against their Policy;
  // the example's cases against the example's rules
  const rulesFrom: RulesFrom = chosen
    ?? (ownPins.length > 0 || !offerExample ? "policy"
      : pins.length > 0 || policyTools.length === 0 ? "example" : "policy");
  const [variant, setVariant] = useState<Variant>("as-is");
  const [raw, setRaw] = useState("");
  const [parseError, setParseError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  // the example's rules come with its cases (seeding is idempotent)
  const example = useQuery({
    queryKey: ["example-rules"],
    queryFn: async () => {
      const r = await api.seedAdapterRuns();
      void qc.invalidateQueries({ queryKey: ["pins"] });
      return r.config;
    },
    // loaded on a click, or when the example's cases are already kept —
    // opening the page never writes into someone's corpus
    enabled: rulesFrom === "example" && (chosen === "example" || pins.some((p) => p.run_id in DEMO_CASES)),
    staleTime: Infinity,
  });

  const policyRules: Record<string, unknown> = rulesOfPolicy(policy);
  const rules = (): Record<string, unknown> | null => {
    if (rulesFrom === "policy") return policyRules;
    if (rulesFrom === "example") return example.data ? exampleVariant(example.data, variant) : null;
    try {
      const parsed: unknown = JSON.parse(raw);
      if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("the rules must be a JSON object");
      }
      return parsed as Record<string, unknown>;
    } catch (e) {
      setParseError(e instanceof Error ? e.message : String(e));
      return null;
    }
  };
  const pick = (from: RulesFrom) => {
    if (from === "json") {
      const current = rulesFrom === "policy" ? policyRules
        : example.data ? exampleVariant(example.data, variant) : {};
      setRaw(JSON.stringify(current, null, 2));
    }
    setChosen(from);
    setParseError(null);
    check.reset();
  };

  const check = useMutation({
    mutationFn: (config: Record<string, unknown>) => api.regression(config),
    // every run is recorded server-side, so the history list is now stale
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["regression-history"] }),
  });
  const run = () => {
    setParseError(null);
    setOpen(null);
    // the example's rules load on first use — a click is that use
    if (rulesFrom === "example" && !example.data) {
      void example.refetch().then((r) => { if (r.data) check.mutate(exampleVariant(r.data, variant)); });
      return;
    }
    const config = rules();
    if (config) check.mutate(config);
  };
  const tryVariant = (v: Variant) => {
    setVariant(v);
    setOpen(null);
    if (example.data) check.mutate(exampleVariant(example.data, v));
  };

  const report = check.data;
  const chip = (on: boolean): React.CSSProperties => ({
    background: on ? "rgba(127,168,204,0.12)" : "none", border: `1px solid ${on ? C.steel : C.line}`,
    borderRadius: 20, padding: "4px 12px", cursor: "pointer", fontFamily: MONO, fontSize: 11.5,
    color: on ? C.text : C.mut,
  });
  const link: React.CSSProperties = {
    background: "none", border: "none", padding: 0, cursor: "pointer", fontFamily: MONO, fontSize: 11.5, color: C.steel,
  };

  return (
    <div style={{ maxWidth: 640, margin: "0 auto" }}>
      <Coach id="regression" title="Safe to ship? — check a rule change before it goes out">
        Every case you kept is replayed under the rules you are about to ship. Two
        sides: attacks must stay blocked, and normal work must keep passing — rules
        that refuse everything would pass a one-sided check, this catches that.
      </Coach>
      <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 6px" }}>Safe to ship?</h1>
      <div style={{ fontSize: 14, color: C.mut, lineHeight: 1.6, marginBottom: 22 }}>
        Change the rules, and every case you kept is replayed under them: attacks must stay
        blocked, normal work must keep passing. No model is called — same answer every time.
      </div>

      {/* the cases */}
      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, letterSpacing: "0.1em", marginBottom: 6 }}>THE CASES</div>
      <div data-testid="cases" style={{ fontFamily: MONO, fontSize: 12, color: C.mut, lineHeight: 1.7, marginBottom: 20 }}>
        {pins.length === 0 ? (
          <>No cases kept yet. On a caught case, <span style={{ color: C.text }}>keep it as a test</span> adds
            it here — or{" "}
            <button onClick={() => pick("example")} style={link}>load the example →</button></>
        ) : (
          <>
            <span style={{ color: C.text }}>{plural(corpus.data?.must_block ?? 0, "attack", "attacks")}</span> that
            must stay blocked · <span style={{ color: C.text }}>{plural(corpus.data?.must_pass ?? 0, "normal flow", "normal flows")}</span> that
            must keep passing
            {ownPins.length === 0 && <span style={{ color: C.dim }}> · all from the example</span>}
            <div style={{ color: C.dim, fontSize: 11 }}>
              more: “keep it as a test” on a caught case, or pin a run in Replay
            </div>
          </>
        )}
      </div>

      {/* the rules */}
      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, letterSpacing: "0.1em", marginBottom: 6 }}>THE RULES TO CHECK</div>
      <div className="flex gap-2 flex-wrap mb-2">
        <button data-testid="rules-policy" onClick={() => pick("policy")} style={chip(rulesFrom === "policy")}
          disabled={policyTools.length === 0}>
          from Policy{policyTools.length ? ` (${plural(policyTools.length, "tool", "tools")})` : " — none yet"}
        </button>
        {offerExample && <button data-testid="rules-example" onClick={() => pick("example")} style={chip(rulesFrom === "example")}>
          the example's
        </button>}
        <button data-testid="rules-json" onClick={() => pick("json")} style={chip(rulesFrom === "json")}>
          edit as JSON
        </button>
      </div>
      <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim, lineHeight: 1.6, marginBottom: 10 }}>
        {rulesFrom === "policy" && (policyTools.length
          ? <>{policyTools.slice(0, 5).map((t) => `${t} — ${policy[t].klass.toLowerCase()}`).join(" · ")}
              {policyTools.length > 5 ? " · …" : ""} · any other tool refused ·{" "}
              <button onClick={() => navigate("policy")} style={{ ...link, fontSize: 11 }}>change in Policy →</button></>
          : <>no tools described yet — <button onClick={() => navigate("policy")} style={{ ...link, fontSize: 11 }}>describe them in Policy →</button></>)}
        {rulesFrom === "example" && (
          <span data-testid="example-variant">
            {variant === "as-is" && "the example agent's rules, as they are"}
            {variant === "looser" && <span style={{ color: C.amber }}>looser: slack_post may post this text even when it came from outside</span>}
            {variant === "stricter" && <span style={{ color: C.amber }}>stricter: notes_write taken away</span>}
          </span>
        )}
      </div>
      {rulesFrom === "json" && (
        <>
          <textarea value={raw} onChange={(e) => setRaw(e.target.value)} rows={8} spellCheck={false}
            aria-label="rules as JSON" className="w-full"
            style={{ background: C.panel2, border: `1px solid ${C.line}`, borderRadius: 6, color: C.text,
                     fontFamily: MONO, fontSize: 12, padding: 10, resize: "vertical", outline: "none" }} />
          {parseError && <div className="mt-1" style={{ fontFamily: MONO, fontSize: 11, color: C.red }}>{parseError}</div>}
        </>
      )}

      <div data-tour="regression-run" className="flex items-center gap-3 flex-wrap" style={{ marginTop: 12, marginBottom: 22 }}>
        <button onClick={run} disabled={check.isPending || example.isFetching
            || (rulesFrom === "policy" && policyTools.length === 0)}
          style={btn({ color: C.text, borderColor: C.steel, fontSize: 12.5, padding: "8px 16px" })}>
          {check.isPending ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />} Check these rules
        </button>
        {rulesFrom === "policy" && policyTools.length === 0 && (
          <span style={{ fontFamily: MONO, fontSize: 11.5, color: C.dim }}>
            Policy is empty —{" "}
            <button onClick={() => navigate("config-builder")} style={link}>list your agent's tools there →</button>
          </span>
        )}
        {rulesFrom === "example" && example.data && (
          <span style={{ fontFamily: MONO, fontSize: 11.5, color: C.dim }}>
            see it fail:{" "}
            <button data-testid="try-looser" onClick={() => tryVariant("looser")} style={link}>a looser rule</button>
            {" · "}
            <button data-testid="try-stricter" onClick={() => tryVariant("stricter")} style={link}>a stricter rule</button>
            {variant !== "as-is" && <>{" · "}<button onClick={() => tryVariant("as-is")} style={link}>back to as is</button></>}
          </span>
        )}
        {check.isError && (
          <span style={{ fontFamily: MONO, fontSize: 11, color: C.red }}>{(check.error as Error).message}</span>
        )}
      </div>

      {report && <Report report={report} open={open} setOpen={setOpen} onRemoved={run}
        exampleRules={rulesFrom === "example"} />}

      <div style={{ marginTop: 28 }}>
        <Fold label="run this check on a schedule (Team)">
          <ScheduleAndHistory parseConfig={() => rules()} />
        </Fold>
        {LAB_SHIPPED && <LabDeploys />}
      </div>
    </div>
  );
}

function Fold({ label, children }: { label: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button onClick={() => setOpen(!open)} style={{ background: "none", border: "none", color: C.mut, fontSize: 12,
        fontFamily: MONO, cursor: "pointer", display: "flex", alignItems: "center", gap: 6, padding: "8px 0" }}>
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />} {label}
      </button>
      {open && <div className="pt-2">{children}</div>}
    </div>
  );
}

function Report({ report, open, setOpen, onRemoved, exampleRules }: {
  report: RegressionReport; open: string | null; setOpen: (v: string | null) => void;
  onRemoved: () => void; exampleRules: boolean;
}) {
  const qc = useQueryClient();
  // A case nothing can be checked against (or one kept by mistake) held every
  // release at "not safe" with no way to take it out.
  const unpin = useMutation({
    mutationFn: (runId: string) => api.unpin(runId),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["pins"] }); onRemoved(); },
  });
  const broken: string[] = [];
  if (report.escaped) broken.push(`${plural(report.escaped, "attack", "attacks")} now ${report.escaped === 1 ? "gets" : "get"} through`);
  if (report.regressed) broken.push(`${plural(report.regressed, "normal flow is", "normal flows are")} now refused`);
  if (report.unanchored) broken.push(`${plural(report.unanchored, "case", "cases")} can't be checked`);
  if (report.skipped.length) broken.push(`${plural(report.skipped.length, "case", "cases")} could not be read`);
  // "the example's" cases are the ones its rules were written for: a canned
  // run kept from Replay counts as demo too, but is not one of them (QA-032)
  const notExample = report.rows.filter((r) => !(r.run_id in DEMO_CASES));
  const onlyExample = report.own_rows === 0 && report.demo_rows > 0 && notExample.length === 0;

  if (report.rows.length === 0 && report.skipped.length === 0) {
    return <div style={{ fontFamily: MONO, fontSize: 12.5, color: C.mut }}>No cases kept yet — nothing to check.</div>;
  }
  return (
    <div data-testid="check-result">
      <div style={{ fontSize: 20, fontWeight: 650, lineHeight: 1.35, marginBottom: 6 }}>
        {broken.length > 0 ? (
          <><span style={{ color: C.red }}>Not safe to ship:</span> {broken.join(", ")}.</>
        ) : onlyExample ? (
          <><span style={{ color: C.green }}>The example holds:</span> its attack is still blocked, its normal flow still passes.</>
        ) : (
          <><span style={{ color: C.green }}>Safe to ship:</span> every attack still blocked, every normal flow still passes.</>
        )}
        {broken.length === 0 && (report.still_refused ?? 0) > 0 && (
          <div style={{ fontFamily: MONO, fontSize: 11.5, fontWeight: 400, color: C.amber, marginTop: 4 }}>
            {plural(report.still_refused!, "normal flow was", "normal flows were")} kept with a refused step, and these
            rules still refuse it — nothing got worse, but nothing got fixed
          </div>
        )}
      </div>
      {onlyExample && (
        <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.dim, marginBottom: 14 }}>
          these are the example's cases, not your agent's — keep cases of your own to check yours
        </div>
      )}
      {exampleRules && notExample.length > 0 && notExample.length < report.rows.length && (
        <div data-testid="not-the-examples" style={{ fontFamily: MONO, fontSize: 11.5, color: C.dim, marginBottom: 14 }}>
          checked against the example's rules too, though they were not written for{" "}
          {notExample.length === 1 ? "it" : "them"}: {notExample.map((r) => caseName(r.run_id, r.label)).join(", ")}
        </div>
      )}

      {report.skipped.length > 0 && (
        <div style={{ marginBottom: 14, fontFamily: MONO, fontSize: 11.5 }}>
          <div style={{ color: C.amber, marginBottom: 6 }}>Kept, but the run could not be read — not checked:</div>
          <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
            {report.skipped.map((p, i) => (
              <div key={p.run_id} className="px-4 py-2" style={{ borderTop: i ? `1px solid ${C.line}` : "none" }}>
                <span style={{ color: C.text }}>{caseName(p.run_id, p.label)}</span>
                <div style={{ color: C.mut, marginTop: 2 }}>{p.reason}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="mt-3" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
        {report.rows.map((r, i) => {
          const knownRefusal = r.result === "passed" && (r.still_refused?.length ?? 0) > 0;
          const unverified = r.side !== "must_block" && (r.unverifiable?.length ?? 0) > 0;
          const st = unverified ? { text: "can't be fully checked", color: C.amber }
            : knownRefusal ? { text: `passes as kept · ${r.still_refused!.length} refused`, color: C.amber }
            : STATUS[r.result];
          const hot = (r.result !== "held" && r.result !== "passed")
            || (r.side !== "must_block" && ((r.unverifiable?.length ?? 0) > 0 || (r.still_refused?.length ?? 0) > 0));
          const isOpen = open === r.run_id;
          return (
            <div key={r.run_id} data-testid={`case-${r.run_id}`} style={{ borderTop: i ? `1px solid ${C.line}` : "none" }}>
              <div onClick={() => setOpen(isOpen ? null : r.run_id)} className="flex items-center gap-3 px-4 py-3"
                style={{ cursor: "pointer" }}>
                <Circle size={9} fill={st.color} color={st.color} />
                <span style={{ fontFamily: MONO, fontSize: 10, color: C.dim, width: 52 }}>
                  {r.side === "must_block" ? "attack" : "normal"}
                </span>
                <span style={{ fontSize: 13, color: hot ? C.text : C.mut, flex: 1 }}>{caseName(r.run_id, r.label)}</span>
                <span style={{ fontFamily: MONO, fontSize: 10.5, fontWeight: hot ? 700 : 400, color: hot ? st.color : C.dim }}>
                  {st.text}
                </span>
                {isOpen ? <ChevronDown size={13} color={C.dim} /> : <ChevronRight size={13} color={C.dim} />}
              </div>
              {isOpen && (
                <div className="px-4 pb-3 flex flex-col gap-2" style={{ paddingLeft: 40 }}>
                  <span style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, lineHeight: 1.6 }}>
                    {r.escaped_denials.length > 0 ? (
                      <span style={{ color: C.red }}>
                        was refused at {r.escaped_denials.map((d) => `${d.tool || "a step"} (step ${d.seq})`).join(", ")} —
                        these rules let it through
                      </span>
                    ) : r.side !== "must_block" && (r.unverifiable?.length ?? 0) > 0 ? (
                      <span style={{ color: C.amber }}>
                        refused at {r.unverifiable!.map((d) => `${d.tool || "a step"} (step ${d.seq})`).join(", ")} when it
                        was kept, but recorded without what drove the refusal — whether these rules still refuse
                        it can't be checked. Keep a newer run of this case
                      </span>
                    ) : r.side !== "must_block" && (r.still_refused?.length ?? 0) > 0 ? (
                      <span style={{ color: C.amber }}>
                        goes as when it was kept — including the refusal at{" "}
                        {r.still_refused!.map((d) => `${d.tool || "a step"} (step ${d.seq})`).join(", ")}.
                        If that refusal is wrong, these rules have not fixed it
                      </span>
                    ) : r.result === "unanchored" && (r.unverifiable?.length ?? 0) > 0 ? (
                      <span style={{ color: C.amber }}>
                        refused at {r.unverifiable!.map((d) => `${d.tool || "a step"} (step ${d.seq})`).join(", ")}, but
                        this run was recorded without what drove the refusal, so other rules can't be checked
                        against it — keep a newer run of this case
                      </span>
                    ) : r.result === "unanchored" ? (
                      <span style={{ color: C.amber }}>
                        nothing was refused in this run, so there is nothing to check — keep a run where
                        something was refused
                      </span>
                    ) : r.new_denial ? (
                      <span style={{ color: r.result === "regressed" ? C.red : C.mut }}>
                        now refused at step {r.new_denial.seq}: {r.new_denial.reason}
                      </span>
                    ) : r.first_divergence != null ? (
                      <>goes differently from step {r.first_divergence}</>
                    ) : r.side === "must_block" ? (
                      <>refused at the same step as when it was kept</>
                    ) : (
                      <>every step goes as it did when it was kept</>
                    )}
                  </span>
                  <div>
                    <button
                      onClick={() => navigate(`explain/${r.run_id}`, {
                        seq: r.escaped_denials[0]?.seq ?? r.still_refused?.[0]?.seq ?? r.new_denial?.seq ?? r.first_divergence ?? undefined,
                        node: r.escaped_denials[0]?.node_id,
                      })}
                      style={btn({ color: C.steel, fontSize: 11, padding: "5px 10px" })}>
                      <Play size={11} /> show where
                    </button>
                    {!r.demo && (
                      <button data-testid={`unpin-${r.run_id}`} disabled={unpin.isPending}
                        onClick={() => {
                          if (window.confirm("Take this case out of the check? Releases will no longer be checked against it.")) {
                            unpin.mutate(r.run_id);
                          }
                        }}
                        style={btn({ color: C.mut, fontSize: 11, padding: "5px 10px", marginLeft: 8 })}>
                        take it out of the check
                      </button>
                    )}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
