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
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, HeartPulse, Check, Circle, MinusCircle } from "lucide-react";
import { api, type HealAttempt, type ProbeFamily, type ProbeHealth } from "../api";
import { C, MONO } from "../theme";
import Coach from "../components/Coach";
import Tooltip from "../components/Tooltip";

const HEALTH_NODE = "banking-assistant";

type Phase = "idle" | "reason" | "healing" | "awaiting" ;

const VERDICT_LINE: Record<ProbeHealth["overall_verdict"], string> = {
  CONSISTENT: "Agent behavior is on baseline.",
  DRIFT_DETECTED: "A probe family drifted from baseline.",
  INCONCLUSIVE: "Not enough probes to answer.",
  CONSISTENCY_ANOMALY: "Responses are suspiciously invariant.",
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
  const [phase, setPhase] = useState<Phase>("idle");
  const [reason, setReason] = useState("");
  const [healError, setHealError] = useState<string | null>(null);
  const [confirmCollateral, setConfirmCollateral] = useState(false);

  const report = useQuery({
    queryKey: ["probe-report", HEALTH_NODE],
    queryFn: () => api.probeReport(HEALTH_NODE),
  });
  // What the node's localizer proposed, and how past cuts ended.
  const repair = useQuery({
    queryKey: ["repair", HEALTH_NODE],
    queryFn: () => api.repair(HEALTH_NODE),
  });

  // The heal is the excision itself: axor-probe shapes the cut from the node's
  // own proposal, and it reaches the node as a signed plane command. It does
  // NOT mark anything healed — the verifying re-probe is the node's job and
  // arrives as a new check. Until then the panel says it is waiting, and now
  // there is something real to wait on.
  const heal = useMutation({
    mutationFn: (why: string) =>
      api.selfHeal(HEALTH_NODE, why, confirmCollateral),
    onSuccess: () => {
      setHealError(null);
      setPhase("awaiting");
      void qc.invalidateQueries({ queryKey: ["probe-report", HEALTH_NODE] });
      void qc.invalidateQueries({ queryKey: ["repair", HEALTH_NODE] });
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
  const history = report.data?.history ?? [];
  const heals = repair.data?.history ?? [];
  const lastHeal: HealAttempt | null = heals[0] ?? null;
  const fams = latest?.families ?? [];
  const drifting = fams.some((f) => f.state === "escaped");

  if (report.isLoading) {
    return <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>loading…</div>;
  }

  return (
    <div style={{ maxWidth: 560, margin: "0 auto" }}>
      <Coach id="health" title="Health — is the agent still the agent you baselined?">
        Probe families compare today's behaviour against an isolated shadow
        baseline. <span style={{ color: C.text }}>DRIFT</span> means a family
        diverged; self-heal cuts the context fragments the node's localizer
        blames — with a required reason, delivered as a signed plane command. It
        never fires automatically: the verdict recommends, you trigger. This is
        not an Eval score and is never blended into one.
      </Coach>

      {latest === null ? (
        <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8, padding: 20 }}>
          <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 8 }}>No health check yet.</div>
          <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, lineHeight: 1.7 }}>
            This node has not posted an axor-probe battery. That is an absence of
            evidence, not a clean bill — the panel will not colour it green.
            <br /><br />
            Batteries run node-side and are posted out-dial to{" "}
            <span style={{ color: C.text }}>POST /v1/plane/{HEALTH_NODE}/probe-report</span>.
            The plane never reaches into your runtime to invoke your agent.
          </div>
        </div>
      ) : (
        <>
          <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 4px" }}>
            {VERDICT_LINE[latest.overall_verdict]}
          </h1>
          <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginBottom: 20 }}>
            {latest.agent_id} · last check {clock(latest.created_ts)} · {latest.probes_sent} probes
            {latest.calibration_status !== "CALIBRATED" && " · uncalibrated"}
          </div>

          <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
            {fams.map((f, i) => (
              <div key={f.family} style={{ borderTop: i ? `1px solid ${C.line}` : "none" }}>
                <div className="flex items-center gap-3 px-4 py-3">
                  {f.state === "unprobed"
                    ? <MinusCircle size={11} color={C.dim} />
                    : <Circle size={9} fill={famColor(f.state)} color={famColor(f.state)} />}
                  <span style={{ fontFamily: MONO, fontSize: 13, color: f.state === "escaped" ? C.text : C.mut, flex: 1 }}>
                    {f.family}
                  </span>
                  <span style={{ fontFamily: MONO, fontSize: 10.5, color: famColor(f.state), fontWeight: f.state === "escaped" ? 700 : 400 }}>
                    {f.state === "unprobed"
                      ? "not probed"
                      : f.state === "escaped"
                        ? (phase === "healing" ? "healing…" : `DRIFT · ${f.escapes}/${f.probes} escaped`)
                        : `OK · 0/${f.probes}`}
                  </span>
                </div>

                {/* self-heal affordance lives with the drift it addresses — and
                    only where there is a cut to make. A battery whose localizer
                    did not run leaves nothing to excise, and offering a button
                    anyway is how a surface ends up promising an action it cannot
                    perform. */}
                {f.state === "escaped" && phase === "idle" && (
                  <div className="px-4 pb-3 flex items-center gap-3" style={{ paddingLeft: 40 }}>
                    <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>
                      {f.escapes} of {f.probes} probes escaped the baseline regime.
                      {canCut
                        ? ` Self-heal excises ${(proposal!.auto_excise.length || escalated.length)} context fragment${(proposal!.auto_excise.length || escalated.length) === 1 ? "" : "s"} the localizer blames.`
                        : proposal === null
                          ? " This battery did not localize the drift, so there is nothing to excise yet."
                          : " The localizer found the tainted fragments do not cause this drift — an excision would be surgery with no verdict behind it."}
                    </span>
                    {canCut && (
                      <Tooltip content={`Remove ${(proposal!.auto_excise.length ? proposal!.auto_excise : escalated).join(", ")} from the agent's context. Delivered as a signed pending_excision command; the adapter applies it at its next intent boundary and reports back. The verifying re-probe arrives as a new check.`}>
                        <button onClick={() => setPhase("reason")}
                          style={{ display: "flex", alignItems: "center", gap: 6, background: "none", border: `1px solid ${C.steel}`, borderRadius: 5, color: C.steel, fontFamily: MONO, fontSize: 11, padding: "5px 12px", cursor: "pointer", whiteSpace: "nowrap" }}>
                          <HeartPulse size={12} /> Self-heal
                        </button>
                      </Tooltip>
                    )}
                  </div>
                )}

                {f.state === "escaped" && phase === "reason" && (
                  <div className="px-4 pb-3 flex flex-col gap-2" style={{ paddingLeft: 40 }}>
                    <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>
                      signed plane command · operator op_ui · cuts{" "}
                      <span style={{ color: C.text }}>
                        {(proposal?.auto_excise.length ? proposal.auto_excise : escalated).join(", ")}
                      </span>{" "}
                      · a running experiment would be marked `intervened`
                    </span>
                    {escalated.length > 0 && (
                      <label className="flex items-start gap-2" style={{ fontFamily: MONO, fontSize: 10.5, color: C.amber, cursor: "pointer" }}>
                        <input type="checkbox" checked={confirmCollateral}
                          onChange={(e) => setConfirmCollateral(e.target.checked)}
                          style={{ marginTop: 2 }} />
                        <span>
                          {escalated.join(", ")} also carr{escalated.length === 1 ? "ies" : "y"} legitimate
                          task content — cutting {escalated.length === 1 ? "it" : "them"} loses work the
                          agent was asked to do. Confirm to include {escalated.length === 1 ? "it" : "them"}.
                        </span>
                      </label>
                    )}
                    <div className="flex gap-2">
                      <input autoFocus value={reason} onChange={(e) => setReason(e.target.value)}
                        placeholder="reason (required) — e.g. drift after prompt update, re-anchoring"
                        style={{ flex: 1, background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: C.text, fontFamily: MONO, fontSize: 11, padding: "6px 8px", outline: "none" }} />
                      {(() => {
                        const armed = Boolean(reason.trim()) && canCut
                          && (!needsCollateral || confirmCollateral);
                        return (
                          <button onClick={trigger} disabled={!armed || heal.isPending}
                            style={{ display: "flex", alignItems: "center", gap: 6, background: "none", border: `1px solid ${armed ? C.steel : C.line}`, borderRadius: 4, color: armed ? C.steel : C.dim, fontFamily: MONO, fontSize: 11, padding: "5px 12px", cursor: armed ? "pointer" : "default" }}>
                            <Check size={12} /> excise & re-probe
                          </button>
                        );
                      })()}
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
                <>excised {lastHeal.target_refs.join(", ")} → awaiting the verifying
                  re-probe. A heal without one is not resolved, so nothing turns green
                  here until the node posts the next check.</>
              ) : lastHeal.resolved ? (
                <>healed by {lastHeal.operator} → re-probe: OK · cut{" "}
                  {lastHeal.target_refs.join(", ")} ({lastHeal.families.join(", ") || "no family named"})</>
              ) : (
                <>healed by {lastHeal.operator} → re-probe: still drifting ·{" "}
                  {lastHeal.target_refs.join(", ")} was cut and{" "}
                  {lastHeal.families.join(", ") || "the family"} still escapes. The
                  excision was not the cause.</>
              )}
            </div>
          )}
          {phase === "awaiting" && !lastHeal && (
            <div style={{ fontFamily: MONO, fontSize: 11, color: C.steel, marginTop: 10 }}>
              excision commanded → awaiting the verifying re-probe.
            </div>
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
              verdict: {latest.overall_verdict} · {latest.escape_count} escaped of {latest.probes_sent} probes
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
        Self-heal never fires automatically on a drift verdict — a behavior-correction loop with no human
        in it is exactly what this product exists to prevent. The verdict recommends; you trigger. What it
        cuts is the localizer's answer, not this panel's: the fragments come from the node, and the ones it
        marked as carrying real work are not removed unless you confirm them by name.
      </div>
    </div>
  );
}
