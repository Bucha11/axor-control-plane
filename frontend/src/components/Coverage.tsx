// What is holding a node down, and what an attestation would discharge.
//
// `covers` on an attestation names FACT IDS — the kernel's contract — and the
// level is max(severity) over the facts no unrevoked attestation covers. So
// "attest branch" is a choice of WHICH fact you are vouching for, with the
// consequence shown before and after: a button that appended a fact covering
// nothing discharged nothing, and this panel is the difference.
//
// The node's own reported level is never overwritten here. It converges when
// the attestation reaches it on its desired-state stream; until then the two
// differ and the divergence is rendered, not hidden.
import { useMutation, useQuery } from "@tanstack/react-query";
import { Shield } from "lucide-react";
import { api, DrivingFact } from "../api";
import { C, MONO, btn } from "../theme";
import Tooltip from "./Tooltip";

const LEVEL_COLOR: Record<string, string> = {
  NORMAL: C.green, CAUTIOUS: C.amber, RESTRICTED: C.amber,
  LOCKED: C.red, TERMINAL: C.red,
};

function levelColor(level: string): string {
  return LEVEL_COLOR[level] ?? C.mut;
}

function Row({ fact, onAttest, pending }: {
  fact: DrivingFact;
  onAttest: (fact: DrivingFact) => void;
  pending: boolean;
}) {
  const covered = fact.covered_by.length > 0;
  return (
    <div style={{ marginBottom: 7 }}>
      <div style={{ fontFamily: MONO, fontSize: 10.5, color: covered ? C.dim : C.text }}>
        <span style={{ color: covered ? C.dim : levelColor("LOCKED") }}>sev {fact.severity}</span>
        {" · "}{fact.fact_type}
        {" · "}<span style={{ color: C.mut }}>{fact.fact_id}</span>
        {fact.causal_root && (
          <>{" · branch "}<span style={{ color: C.steel }}>{fact.causal_root}</span></>
        )}
      </div>
      {fact.reason && (
        <div style={{ fontFamily: MONO, fontSize: 10, color: C.mut, marginTop: 1 }}>
          {fact.reason}
        </div>
      )}
      {covered ? (
        <div style={{ fontFamily: MONO, fontSize: 10, color: C.green, marginTop: 2 }}>
          discharged · attested by {fact.covered_by.join(", ")}
        </div>
      ) : (
        <button
          onClick={() => onAttest(fact)}
          disabled={pending}
          style={btn({ color: C.mut, fontSize: 10.5, padding: "3px 8px", marginTop: 3 })}
        >
          <Shield size={11} /> attest this
        </button>
      )}
    </div>
  );
}

export default function Coverage({ nodeId, refetchMs }: {
  nodeId: string;
  refetchMs?: number;
}) {
  const cov = useQuery({
    queryKey: ["coverage", nodeId],
    queryFn: () => api.nodeCoverage(nodeId),
    refetchInterval: refetchMs,
  });

  const attest = useMutation({
    mutationFn: ({ fact, reason }: { fact: DrivingFact; reason: string }) =>
      api.appendFact(nodeId, {
        fact_id: `att_${fact.fact_id}_${Date.now()}`,
        fact_type: "operator_attestation",
        reason,
        operator: "op_ui",
        run_id: cov.data?.run_id ?? "",
        // The kernel's contract: covering the fact id is what discharges it
        // from the level recompute.
        covers: [fact.fact_id],
        // Sentinel's: the branch this vouches for, so the same act shows up on
        // that branch's attestation history in Replay.
        ...(fact.causal_root ? { causal_root: fact.causal_root } : {}),
      }),
    onSuccess: () => void cov.refetch(),
  });

  const onAttest = (fact: DrivingFact) => {
    const reason = window.prompt(
      `Attest ${fact.fact_id} — what did you check? (required, recorded, append-only):`,
    );
    if (reason && reason.trim()) attest.mutate({ fact, reason: reason.trim() });
  };

  if (cov.isPending || !cov.data) return null;
  const { facts, level, reported_level: reported } = cov.data;
  if (cov.isError) {
    return (
      <div style={{ fontFamily: MONO, fontSize: 11, color: C.red, marginTop: 10 }}>
        {(cov.error as Error).message}
      </div>
    );
  }
  if (facts.length === 0) {
    return (
      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 10 }}>
        nothing is holding this node down — no recorded fact to attest.
      </div>
    );
  }

  return (
    <div className="mt-3 p-3" style={{ background: C.bg, border: `1px solid ${C.line}`, borderRadius: 6 }}>
      <Tooltip content="Every recorded fact that counts toward this node's degradation. Vouching for one discharges it from the level — with your reason, append-only, and revocable as its own event.">
        <div style={{ fontFamily: MONO, fontSize: 11, color: C.text, marginBottom: 8 }}>
          facts behind this node's level
        </div>
      </Tooltip>
      {facts.map((f) => (
        <Row key={f.fact_id} fact={f} onAttest={onAttest} pending={attest.isPending} />
      ))}
      <div style={{ fontFamily: MONO, fontSize: 10, color: C.mut, borderTop: `1px solid ${C.line}`, paddingTop: 6 }}>
        with coverage: <span style={{ color: levelColor(level) }}>{level}</span>
        {level !== reported && (
          <>
            {" · node still reports "}
            <span style={{ color: levelColor(reported) }}>{reported}</span>
            {" — it converges when it applies the attestation"}
          </>
        )}
      </div>
      {attest.isError && (
        <div style={{ fontFamily: MONO, fontSize: 10, color: C.red, marginTop: 5 }}>
          {(attest.error as Error).message}
        </div>
      )}
    </div>
  );
}
