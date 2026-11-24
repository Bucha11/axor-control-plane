// Behavioral health check + reason-required self-heal (ui-spec 8.2, 8.2.1).
//
// The per-family verdicts are the node's last posted axor-probe battery
// (GET /v1/plane/{node}/probe-report). Self-heal is the excision that battery's
// localizer proposed, delivered to the node as a signed `pending_excision`
// command — the cut the adapter applies at its next intent boundary.
//
// It used to append an `operator_attestation` fact and then say it was awaiting
// the verifying re-probe: a record that somebody intervened, beside a context
// nothing had touched. The node's desired state carried no excision, so
// `take_pending_excision` returned null and no fragment was ever removed. The
// button named an action that did not happen, and the phase it entered implied
// a repair to verify.
//
// Three rules this panel exists to hold:
//   * Drift is not an Eval metric. This answers "has my agent changed?", never
//     "does my agent lie under fault?" — separate panels, separate vocabulary.
//     A drift-red agent with a green integrity score is legitimate (spec 8.2).
//   * No optimistic green. A family with no probes is `unprobed`, not OK; a
//     heal is not resolved until a verifying re-probe arrives as a NEW check.
//   * Nothing is cut without a verdict behind it. The refs come from the node's
//     localizer, never from this panel; fragments it ESCALATED (they carry real
//     task content, so cutting them has collateral) need a second, explicit
//     confirmation, and axor-probe refuses the request without one.
import { useEffect, useState } from "react";
import { navigate, useRoute } from "../router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, HeartPulse, Check, Circle, MinusCircle } from "lucide-react";
import { api, type HealAttempt, type ProbeFamily, type ProbeHealth } from "../api";
import { C, MONO, btn } from "../theme";
import Coach from "../components/Coach";
import Tooltip from "../components/Tooltip";
import { NO_CUT, behindProxy, cannotCut } from "../agentName";

// The demo battery's node: shown only when it is actually listed — it used to
// be the default and was mixed into every workspace's agents as if it were one.
const HEALTH_NODE = "banking-assistant";
/** The example fleet's scraper (backend demo.FLEET_PROBE_NODE): it has no
 * runtime of its own, so its re-probe is played on request. */
export const EXAMPLE_PROBE_NODE = "tree-scraper";

type Phase = "idle" | "reason" | "healing" | "awaiting" ;

const VERDICT_LINE: Record<ProbeHealth["overall_verdict"], string> = {
  CONSISTENT: "It answers the way it used to.",
  DRIFT_DETECTED: "It answers differently than it used to.",
  INCONCLUSIVE: "Not enough test questions to tell.",
  CONSISTENCY_ANOMALY: "Its answers are suspiciously all the same.",
};

const FAMILY_WORDS: Record<string, string> = {
  data_disclosure: "giving out data",
  scope_expansion: "going beyond the task",
  instruction_following: "following instructions",
};

function famColor(state: ProbeFamily["state"]): string {
  if (state === "escaped") return C.amber;
  if (state === "unprobed") return C.dim;
  return C.green;
}

function clock(iso: string): string {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toTimeString().slice(0, 5);
}

export default function Health() {
  const qc = useQueryClient();
  const route = useRoute();
  // `#/health?node=X` (the Agents list links here) opens on that node.
  const [node, setNode] = useState(route.query.node || "");
  const [chosen, setChosen] = useState(Boolean(route.query.node));
  const nodes = useQuery({ queryKey: ["nodes"], queryFn: api.nodes });
  // the examples hide once the workspace's own agents report, as on every
  // screen — unless one was opened on purpose ("see an example")
  const own = (nodes.data ?? []).filter((n) => !n.example_hidden);
  const nodeIds = Array.from(new Set([node, ...own.map((n) => n.node_id)])).filter(Boolean);
  const shownNode = (nodes.data ?? []).find((n) => n.node_id === node);
  const [phase, setPhase] = useState<Phase>("idle");
  const [reason, setReason] = useState("");
  const [healError, setHealError] = useState<string | null>(null);
  const [confirmCollateral, setConfirmCollateral] = useState(false);
  // The newest heal id before this one was requested: only a LATER attempt's
  // re-probe ends the wait (the stale list still holds the previous outcome).
  const [awaitAfterId, setAwaitAfterId] = useState(-1);

  // a check lands after every run: a page left open must show the new one,
  // not "same as before" beside an agent that has drifted since
  const report = useQuery({
    queryKey: ["probe-report", node],
    queryFn: () => api.probeReport(node),
    enabled: Boolean(node),
    refetchInterval: 10_000,
  });
  // Opened on the default node with nothing to show while the example fleet IS
  // loaded: show the node that has a check, not an empty panel.
  const listed = (nodes.data ?? []).map((n) => n.node_id);
  useEffect(() => {
    if (route.query.node && route.query.node !== node) {
      setNode(route.query.node);
      setChosen(true);
    }
  }, [route.query.node]); // eslint-disable-line react-hooks/exhaustive-deps
  // Not sent to a node: open on the one that needs a look — a drifting check
  // first, then any node that has posted one. Never an empty default panel
  // beside a node with a proposed cut waiting.
  useEffect(() => {
    if (chosen || !own.length) return;
    const drifting = own.find((n) => n.probe?.verdict === "DRIFT_DETECTED");
    const checked = own.find((n) => n.probe);
    // ...else the workspace's first agent, never a made-up default
    const pick = drifting ?? checked ?? (node ? undefined : own[0]);
    if (pick && pick.node_id !== node) setNode(pick.node_id);
  }, [chosen, nodes.data]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!chosen && node === HEALTH_NODE && report.isSuccess && report.data.latest === null
        && listed.includes(EXAMPLE_PROBE_NODE)) {
      setNode(EXAMPLE_PROBE_NODE);
    }
  }, [node, report.isSuccess, report.data, listed.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps
  // A production agent's drift check, on example data: the example fleet posts
  // one, but once the workspace's own agents report the lists hide examples,
  // so it was out of reach exactly when someone wanted to see what they'd get.
  const showExample = useMutation({
    mutationFn: async () => { await api.seedFleet(); },
    onSuccess: () => pickNode(EXAMPLE_PROBE_NODE),
  });
  const reprobe = useMutation({
    mutationFn: api.fleetReprobe,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["probe-report", node] });
      void qc.invalidateQueries({ queryKey: ["repair", node] });
    },
  });
  // What the node's localizer proposed, and how past cuts ended.
  const repair = useQuery({
    queryKey: ["repair", node],
    queryFn: () => api.repair(node),
    // no node chosen yet: there is nothing to ask (it was GET /v1/plane//repair)
    enabled: Boolean(node),
    refetchInterval: 10_000,
  });

  // The heal is the excision itself: axor-probe shapes the cut from the node's
  // own proposal, and it reaches the node as a signed plane command. It does
  // NOT mark anything healed — the verifying re-probe is the node's job and
  // arrives as a new check. Until then the panel says it is waiting, and now
  // there is something real to wait on.
  const heal = useMutation({
    mutationFn: (why: string) =>
      api.selfHeal(node, why, confirmCollateral),
    onSuccess: () => {
      setHealError(null);
      setAwaitAfterId(repair.data?.history[0]?.id ?? -1);
      setPhase("awaiting");
      void qc.invalidateQueries({ queryKey: ["probe-report", node] });
      void qc.invalidateQueries({ queryKey: ["repair", node] });
    },
    onError: (err: Error) => {
      setHealError(err.message);
      setPhase("reason");
    },
  });

  const proposal = repair.data?.proposal ?? null;
  // The localizer escalated: the fragments it would cut also carry legitimate
  // task content, so removing them has collateral. axor-probe refuses the
  // request until the operator confirms those fragments by name.
  const escalated = proposal?.escalate ?? [];
  const needsCollateral = escalated.length > 0 && (proposal?.auto_excise.length ?? 0) === 0;
  const canCut = proposal !== null && proposal.verdict !== "no_drift_from_taint";

  const trigger = () => {
    if (!reason.trim() || !canCut) return;
    if (needsCollateral && !confirmCollateral) return;
    setHealError(null);
    setPhase("healing");
    heal.mutate(reason.trim());
  };

  const latest = report.data?.latest ?? null;
  const graded = report.data?.drift_case ?? null;
  const history = report.data?.history ?? [];
  const heals = repair.data?.history ?? [];
  const lastHeal: HealAttempt | null = heals[0] ?? null;
  const fams = latest?.families ?? [];
  const drifting = fams.some((f) => f.state === "escaped");
  // One reason form for the whole battery — the cut is one proposal, not one
  // per family — anchored under the first drifting family.
  const firstEscaped = fams.find((f) => f.state === "escaped")?.family ?? null;

  // The awaited re-probe arrived (or the drift is gone): the panel can offer
  // the next heal instead of waiting forever.
  useEffect(() => {
    if (phase !== "awaiting") return;
    const verified = lastHeal !== null && lastHeal.id > awaitAfterId
      && lastHeal.reprobe_verdict !== null;
    if (verified || !drifting) setPhase("idle");
  }, [phase, drifting, lastHeal, awaitAfterId]);

  const pickNode = (id: string): void => {
    setNode(id);
    setChosen(true);
    setPhase("idle");
    setReason("");
    setHealError(null);
    setConfirmCollateral(false);
  };

  const picker = (
    <div className="flex items-center gap-2 mb-3" style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>
      agent
      <select value={node} onChange={(e) => pickNode(e.target.value)} aria-label="health node"
        style={{ background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: C.text, fontFamily: MONO, fontSize: 11, padding: "4px 8px", outline: "none" }}>
        {nodeIds.map((id) => <option key={id} value={id}>{id}</option>)}
      </select>
    </div>
  );

  if (!node) {
    return (
      <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut }}>
        No agent has reported yet — a health check is about an agent that runs.
      </div>
    );
  }
  if (report.isLoading) {
    return <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>loading…</div>;
  }

  return (
    <div style={{ maxWidth: 560, margin: "0 auto" }}>
      <Coach id="health" title="Health — does the agent still answer the way it used to?">
        A set of test questions — about giving out data, going beyond the task, following
        instructions — is asked again and compared with how the agent answered before. When it
        answers differently, the check traces which piece of its context changed it, and you can
        remove that piece. Nothing is removed without you.
      </Coach>

      {picker}

      {report.isError ? (
        <div style={{ fontFamily: MONO, fontSize: 11, color: C.red }}>
          cannot read the health check: {(report.error as Error).message}
        </div>
      ) : latest === null ? (
        <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8, padding: 20 }}>
          {behindProxy(shownNode) ? (
            <div data-testid="health-proxy">
              <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 8 }}>Not available behind the proxy.</div>
              <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, lineHeight: 1.7 }}>
                {node} is guarded by the proxy, which sees its tool calls and never its model — and a
                health check asks the agent itself test questions. Wrap the agent in code and the
                check runs beside it; the proxy's guard keeps working the same way.
                <br /><br />
                <button onClick={() => navigate("connect?for=production&how=code")} data-testid="health-to-wrap"
                  style={btn({ color: C.text, borderColor: C.steel, fontSize: 11, padding: "4px 10px" })}>
                  wrap it in code →
                </button>
              </div>
              <div className="mt-3">
                <button onClick={() => showExample.mutate()} disabled={showExample.isPending}
                  data-testid="health-example"
                  style={{ background: "none", border: "none", padding: 0, cursor: "pointer",
                           fontFamily: MONO, fontSize: 11, color: C.steel }}>
                  {showExample.isPending ? "loading…" : "see what a drift check looks like (example) →"}
                </button>
              </div>
            </div>
          ) : (
            <>
              <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 8 }}>No health check yet.</div>
              <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, lineHeight: 1.7 }}>
                This agent has not sent a check, so nothing here is green either.
                <br /><br />
                A check runs beside a wrapped agent: axor-probe asks it test questions and the
                agent&apos;s plane link sends the result here (axor-probe README, “Reporting to the
                Control Plane”). The plane never starts your agent itself.
              </div>
              <div className="mt-3">
                <button onClick={() => showExample.mutate()} disabled={showExample.isPending}
                  data-testid="health-example"
                  style={{ background: "none", border: "none", padding: 0, cursor: "pointer",
                           fontFamily: MONO, fontSize: 11, color: C.steel }}>
                  {showExample.isPending ? "loading…" : "see what a drift check looks like (example) →"}
                </button>
              </div>
            </>
          )}
        </div>
      ) : (
        <>
          {node === EXAMPLE_PROBE_NODE && (
            <div data-testid="health-example-badge" style={{ fontFamily: MONO, fontSize: 11, color: C.amber, marginBottom: 6 }}>
              example — a production agent&apos;s drift check, on example data
            </div>
          )}
          <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 4px" }}>
            {VERDICT_LINE[latest.overall_verdict]}
          </h1>
          <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginBottom: 20 }}>
            {latest.agent_id} · checked {clock(latest.created_ts)} · {latest.probes_sent} test questions
            {latest.calibration_status !== "CALIBRATED" && " · thresholds not calibrated yet"}
          </div>

          <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
            {fams.map((f, i) => (
              <div key={f.family} style={{ borderTop: i ? `1px solid ${C.line}` : "none" }}>
                <div className="flex items-center gap-3 px-4 py-3">
                  {f.state === "unprobed"
                    ? <MinusCircle size={11} color={C.dim} />
                    : <Circle size={9} fill={famColor(f.state)} color={famColor(f.state)} />}
                  <span style={{ fontFamily: MONO, fontSize: 13, color: f.state === "escaped" ? C.text : C.mut, flex: 1 }}>
                    {FAMILY_WORDS[f.family] ?? f.family.replace(/_/g, " ")}
                  </span>
                  <span style={{ fontFamily: MONO, fontSize: 10.5, color: famColor(f.state), fontWeight: f.state === "escaped" ? 700 : 400 }}>
                    {f.state === "unprobed"
                      ? "not checked"
                      : f.state === "escaped"
                        ? (phase === "healing" ? "removing…" : `${f.escapes} of ${f.probes} answered differently`)
                        : `same as before · ${f.probes} questions`}
                  </span>
                </div>

                {/* self-heal affordance lives with the drift it addresses — and
                    only where there is a cut to make. A battery whose localizer
                    did not run leaves nothing to excise, and offering a button
                    anyway is how a surface ends up promising an action it cannot
                    perform. */}
                {f.family === firstEscaped && phase === "idle" && (
                  <div className="px-4 pb-3 flex items-center gap-3" style={{ paddingLeft: 40 }}>
                    <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>
                      {f.escapes} of {f.probes} test questions got a different answer than before.
                      {canCut
                        ? ` The check traced it to ${(proposal!.auto_excise.length || escalated.length)} piece${(proposal!.auto_excise.length || escalated.length) === 1 ? "" : "s"} of its context — removing ${(proposal!.auto_excise.length || escalated.length) === 1 ? "it" : "them"} should bring it back.`
                        : proposal === null
                          ? " The check did not trace it to a piece of its context, so there is nothing to remove yet."
                          : " The pieces of context that came from outside are not what changed it — removing them would not help."}
                    </span>
                    {canCut && cannotCut(shownNode) && (
                      <span data-testid="no-cut" style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, maxWidth: 320 }}>{NO_CUT}</span>
                    )}
                    {canCut && !cannotCut(shownNode) && (
                      <Tooltip content={`Remove ${(proposal!.auto_excise.length ? proposal!.auto_excise : escalated).join(", ")} from the agent's context at its next turn; it is checked again afterwards.`}>
                        <button onClick={() => setPhase("reason")}
                          style={{ display: "flex", alignItems: "center", gap: 6, background: "none", border: `1px solid ${C.steel}`, borderRadius: 5, color: C.steel, fontFamily: MONO, fontSize: 11, padding: "5px 12px", cursor: "pointer", whiteSpace: "nowrap" }}>
                          <HeartPulse size={12} /> Remove the cause
                        </button>
                      </Tooltip>
                    )}
                  </div>
                )}

                {f.family === firstEscaped && phase === "reason" && (
                  <div className="px-4 pb-3 flex flex-col gap-2" style={{ paddingLeft: 40 }}>
                    <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>
                      removes{" "}
                      <span style={{ color: C.text }}>
                        {(proposal?.auto_excise.length ? proposal.auto_excise : escalated).join(", ")}
                      </span>{" "}
                      from its context at the next turn · a chaos test running now would be marked as interfered with
                    </span>
                    {escalated.length > 0 && (
                      <label className="flex items-start gap-2" style={{ fontFamily: MONO, fontSize: 10.5, color: C.amber, cursor: "pointer" }}>
                        <input type="checkbox" checked={confirmCollateral}
                          onChange={(e) => setConfirmCollateral(e.target.checked)}
                          style={{ marginTop: 2 }} />
                        <span>
                          {escalated.join(", ")} also carr{escalated.length === 1 ? "ies" : "y"} part of the
                          task — removing {escalated.length === 1 ? "it" : "them"} loses work the agent was asked
                          to do. Tick to remove {escalated.length === 1 ? "it" : "them"} anyway.
                        </span>
                      </label>
                    )}
                    <div className="flex gap-2">
                      <input autoFocus value={reason} onChange={(e) => setReason(e.target.value)}
                        placeholder="why (required) — e.g. changed after the prompt update"
                        style={{ flex: 1, background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: C.text, fontFamily: MONO, fontSize: 11, padding: "6px 8px", outline: "none" }} />
                      {(() => {
                        const armed = Boolean(reason.trim()) && canCut
                          && (!needsCollateral || confirmCollateral);
                        return (
                          <button onClick={trigger} disabled={!armed || heal.isPending}
                            style={{ display: "flex", alignItems: "center", gap: 6, background: "none", border: `1px solid ${armed ? C.steel : C.line}`, borderRadius: 4, color: armed ? C.steel : C.dim, fontFamily: MONO, fontSize: 11, padding: "5px 12px", cursor: armed ? "pointer" : "default" }}>
                            <Check size={12} /> remove and check again
                          </button>
                        );
                      })()}
                      <button onClick={() => { setPhase("idle"); setHealError(null); }}
                        disabled={heal.isPending}
                        style={{ background: "none", border: `1px solid ${C.line}`, borderRadius: 4, color: C.mut, fontFamily: MONO, fontSize: 11, padding: "5px 12px", cursor: "pointer" }}>
                        cancel
                      </button>
                    </div>
                    {healError && (
                      <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.red }}>{healError}</span>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>

          {/* The heal->verify pair, as one line. THREE states, not two: a cut
              the node has not reported back on yet is not a cut that failed. */}
          {lastHeal && (
            <div style={{ fontFamily: MONO, fontSize: 11, marginTop: 10,
                          color: lastHeal.resolved === true ? C.green
                               : lastHeal.resolved === false ? C.amber : C.steel }}>
              {lastHeal.reprobe_verdict === null ? (
                <>removed {lastHeal.target_refs.join(", ")} → waiting for the agent's next check.
                  Nothing turns green here until it comes back.
                  {node !== EXAMPLE_PROBE_NODE && (
                    <span style={{ display: "block", marginTop: 6, color: C.dim }}>
                      the agent drops it from its context at its next turn — if it runs
                      axor-core 0.12.1 or later; an older one gets the command and cannot apply it.
                    </span>
                  )}
                  {node === EXAMPLE_PROBE_NODE && (
                    <span style={{ display: "block", marginTop: 8 }}>
                      {/* a real node consumes the cut and re-probes on its own;
                          the example node has no runtime, so it is played here */}
                      <button
                        onClick={() => reprobe.mutate()}
                        disabled={reprobe.isPending}
                        style={btn({ color: C.steel, borderColor: C.steel, fontSize: 11, padding: "5px 12px" })}
                      >
                        {reprobe.isPending ? "checking…" : "check the example agent again"}
                      </button>
                      {reprobe.isError && (
                        <span style={{ color: C.red, marginLeft: 8 }}>{(reprobe.error as Error).message}</span>
                      )}
                    </span>
                  )}</>
              ) : lastHeal.resolved ? (
                <>removed by {lastHeal.operator} → checked again: answers as before · removed{" "}
                  {lastHeal.target_refs.join(", ")}</>
              ) : (
                <>removed by {lastHeal.operator} → checked again: still answers differently ·{" "}
                  {lastHeal.target_refs.join(", ")} was removed and it did not help — that was not the cause.</>
              )}
            </div>
          )}
          {phase === "awaiting" && !lastHeal && (
            <div style={{ fontFamily: MONO, fontSize: 11, color: C.steel, marginTop: 10 }}>
              removal sent → waiting for the agent's next check.
            </div>
          )}

          {/* The same battery in Eval's vocabulary — the tier, not a score.
              Two reports that both read DRIFT_DETECTED are not the same news:
              one backed by canary escapes is a structural fact about the probe
              output, the other is a judge-graded anomaly discounted again for
              being uncalibrated. The plane kept only the verdict string, so
              they rendered — and paged — identically.

              It is labelled as evidence and never as a number to compare
              agents by: BEHAVIORAL_DRIFT is Experimental and excluded from the
              integrity score whatever its confidence (ui-spec 8.2). */}
          {graded && (
            <details style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 10, lineHeight: 1.7 }}>
              <summary style={{ cursor: "pointer" }}>how this was graded</summary>
              graded as <span style={{ color: C.text }}>{graded.deviation}</span> ·{" "}
              <Tooltip content={graded.verdict_source === "deterministic"
                ? "Escape-backed: a canary or structural readout escaped the clean baseline. A fact about the probe output, not a judgement about it."
                : "Judge-graded: no canary escape behind this verdict, so the confidence is interpretive — and discounted again while the battery is uncalibrated."}>
                <span style={{ color: graded.verdict_source === "deterministic" ? C.text : C.mut,
                               borderBottom: `1px dotted ${C.line}`, cursor: "help" }}>
                  {graded.verdict_source}
                </span>
              </Tooltip>{" "}
              · confidence {graded.confidence.toFixed(2)}
              <br />
              Evidence, not a score — never blended into Scenario Delta or the integrity
              score. This asks whether your agent changed; a chaos test asks whether it lies
              when a tool breaks.
            </details>
          )}

          {/* The drift series — only once there is something to compare against. */}
          {history.length >= 2 && (
            <div className="flex items-center gap-2 mt-3" style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>
              <span>checks:</span>
              {history.map((h) => (
                <Tooltip key={h.id} content={`${clock(h.created_ts)} · ${h.overall_verdict} · ${h.escape_count} escaped / ${h.probes_sent} probes`}>
                  <Circle size={8} fill={h.escape_count > 0 ? C.amber : C.green}
                    color={h.escape_count > 0 ? C.amber : C.green} />
                </Tooltip>
              ))}
            </div>
          )}

          <div className="flex items-center justify-between mt-3">
            <span style={{ fontFamily: MONO, fontSize: 11, color: drifting ? C.amber : C.dim }}>
              {latest.escape_count} of {latest.probes_sent} test questions answered differently
            </span>
            <Tooltip content="Re-read the node's latest posted battery, and how the last excision ended. Batteries are run node-side — the plane does not dial into your runtime to start one.">
              {/* Both queries. The heal->verify line is fed by /repair, not by
                  the battery, so refreshing only the battery left the panel
                  showing "awaiting the verifying re-probe" next to a re-probe
                  it had just fetched. */}
              <button onClick={() => { void report.refetch(); void repair.refetch(); }}
                style={{ display: "flex", alignItems: "center", gap: 6, background: "none", border: `1px solid ${C.line}`, borderRadius: 5, color: C.mut, fontFamily: MONO, fontSize: 11, padding: "5px 12px", cursor: "pointer" }}>
                <RefreshCw size={12} /> Refresh
              </button>
            </Tooltip>
          </div>
        </>
      )}

      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 14, lineHeight: 1.7 }}>
        Nothing is removed on its own: the check suggests, you decide. What is removed is what the check
        traced the change to; a piece that also carries part of the task stays unless you tick it.
      </div>
    </div>
  );
}
