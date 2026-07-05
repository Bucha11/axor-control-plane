// Control: a tree that is quiet when healthy (main-tabs mockup, ControlTab).
// Wired to /v1/plane/nodes; divergence between desired and reported is rendered, not hidden.
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Circle, GitBranch, Pause, Play, Shield, Square, Syringe } from "lucide-react";
import { api, NodeInfo } from "../api";
import { C, MONO, btn } from "../theme";

const REFETCH_MS = 5000;

function isHot(n: NodeInfo): boolean {
  return (n.reported?.level ?? "NORMAL") !== "NORMAL";
}

export default function ControlTab() {
  const qc = useQueryClient();
  const nodes = useQuery({
    queryKey: ["nodes"],
    queryFn: api.nodes,
    refetchInterval: REFETCH_MS,
  });
  const [sel, setSel] = useState<string | null>(null);
  const [more, setMore] = useState(false);
  const [cmdError, setCmdError] = useState<string | null>(null);

  const command = useMutation({
    mutationFn: ({ nodeId, version, state }: {
      nodeId: string; version: number; state: Record<string, unknown>;
    }) => api.command(nodeId, version, state),
    onSuccess: () => {
      setCmdError(null);
      void qc.invalidateQueries({ queryKey: ["nodes"] });
    },
    onError: (err: Error) => setCmdError(err.message),
  });

  if (nodes.isLoading) {
    return (
      <div style={{ maxWidth: 640, margin: "0 auto", fontFamily: MONO, fontSize: 12.5, color: C.mut }}>
        loading nodes…
      </div>
    );
  }

  const list = nodes.data ?? [];
  if (list.length === 0) {
    return (
      <div style={{ maxWidth: 640, margin: "0 auto", fontFamily: MONO, fontSize: 12.5, color: C.mut }}>
        No governed nodes connected. Control unlocks with the adapter.
      </div>
    );
  }

  const node = list.find((n) => n.node_id === sel) ?? null;
  const anyHot = list.some(isHot);

  const desired = node?.desired ?? null;
  const reported = node?.reported ?? null;
  const applying =
    desired != null && desired.version > (reported?.applied_version ?? 0);
  const paused = desired?.state.paused === true;
  const stopped = desired?.state.stopped === true;

  const send = (state: Record<string, unknown>) => {
    if (!node) return;
    setCmdError(null);
    command.mutate({
      nodeId: node.node_id,
      version: (node.desired?.version ?? 0) + 1,
      state,
    });
  };

  return (
    <div style={{ maxWidth: 640, margin: "0 auto" }}>
      <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 4px" }}>
        {anyHot ? <>One agent needs attention.</> : <>All agents healthy.</>}
      </h1>
      <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginBottom: 20 }}>
        {list.length} node{list.length === 1 ? "" : "s"} · live
      </div>

      <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
        {list.map((n, i) => {
          const hot = isHot(n);
          return (
            <div
              key={n.node_id}
              onClick={() => {
                setSel(sel === n.node_id ? null : n.node_id);
                setMore(false);
                setCmdError(null);
              }}
              className="flex items-center gap-2.5 px-4 py-3"
              style={{
                cursor: "pointer",
                borderTop: i === 0 ? "none" : `1px solid ${C.line}`,
                background: sel === n.node_id ? "rgba(127,168,204,0.05)" : "transparent",
              }}
            >
              <Circle size={8} fill={hot ? C.amber : C.green} color={hot ? C.amber : C.green} />
              <span style={{ fontFamily: MONO, fontSize: 13, color: C.text, flex: 1 }}>
                {n.node_id}
              </span>
              {hot && (
                <span style={{ fontFamily: MONO, fontSize: 10, color: C.amber }}>
                  {n.reported?.level}
                </span>
              )}
            </div>
          );
        })}
      </div>

      {node && (
        <div className="mt-3 p-4" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
          <div className="flex items-center justify-between mb-3">
            <span style={{ fontFamily: MONO, fontSize: 13, color: C.text }}>{node.node_id}</span>
            {applying ? (
              <span style={{ fontFamily: MONO, fontSize: 11, color: C.amber }}>
                applying {Object.keys(desired?.state ?? {}).join(", ")}…
              </span>
            ) : stopped ? (
              <span style={{ fontFamily: MONO, fontSize: 11, color: C.red }}>stopped</span>
            ) : paused ? (
              <span style={{ fontFamily: MONO, fontSize: 11, color: C.amber }}>paused</span>
            ) : (
              <span style={{ fontFamily: MONO, fontSize: 11, color: C.green }}>running</span>
            )}
          </div>

          <div className="mb-3" style={{ fontFamily: MONO, fontSize: 11, color: C.mut, lineHeight: 1.7 }}>
            desired{" "}
            <span style={{ color: C.text }}>
              v{desired?.version ?? 0} {JSON.stringify(desired?.state ?? {})}
            </span>
            <br />
            reported{" "}
            <span style={{ color: applying ? C.amber : C.text }}>
              v{reported?.applied_version ?? 0} · {reported?.level ?? "unknown"}
              {reported?.budget_remaining != null
                ? ` · budget ${reported.budget_remaining}`
                : ""}
            </span>
          </div>

          <div className="flex gap-2">
            <button
              onClick={() => send({ paused: !paused })}
              disabled={stopped || command.isPending}
              style={btn({
                background: C.bg,
                color: stopped ? C.dim : C.text,
                fontSize: 12,
                padding: "7px 14px",
                cursor: stopped ? "default" : "pointer",
                opacity: stopped ? 0.5 : 1,
              })}
            >
              {paused ? <Play size={13} /> : <Pause size={13} />} {paused ? "Resume" : "Pause"}
            </button>
            <button
              onClick={() => setMore(!more)}
              style={{ background: "none", border: "none", color: C.mut, fontFamily: MONO, fontSize: 12, cursor: "pointer" }}
            >
              more…
            </button>
          </div>

          {more && (
            <div className="flex gap-2 mt-2 flex-wrap">
              <button
                onClick={() => send({ stopped: true })}
                disabled={stopped || command.isPending}
                style={btn({ color: stopped ? C.dim : C.mut, fontSize: 11, padding: "6px 10px" })}
              >
                <Square size={12} /> Stop
              </button>
              {([
                ["Replan", GitBranch],
                ["Inject next turn", Syringe],
                ["Attest branch", Shield],
              ] as const).map(([label, Icon]) => (
                <button
                  key={label}
                  disabled
                  title="adapter integration — phase 5"
                  style={btn({ color: C.dim, fontSize: 11, padding: "6px 10px", cursor: "default", opacity: 0.6 })}
                >
                  <Icon size={12} /> {label}
                </button>
              ))}
            </div>
          )}

          {cmdError && (
            <div className="mt-3" style={{ fontFamily: MONO, fontSize: 11, color: C.red }}>
              {cmdError}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
