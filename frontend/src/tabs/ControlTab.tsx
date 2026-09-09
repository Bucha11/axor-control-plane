// Control: a tree that is quiet when healthy (main-tabs mockup, ControlTab).
// Wired to /v1/plane/nodes; divergence between desired and reported is rendered, not hidden.
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Circle, Gauge, GitBranch, Pause, Play, Square, Syringe } from "lucide-react";
import { api, NodeInfo } from "../api";
import Coverage from "../components/Coverage";
import TopologyGraph, { PeerCard } from "../components/TopologyGraph";
import { isAdapter, useApp } from "../store";
import { C, MONO, btn } from "../theme";
import Locked from "../components/Locked";
import Coach from "../components/Coach";
import Tooltip from "../components/Tooltip";

const REFETCH_MS = 5000;

function isHot(n: NodeInfo): boolean {
  return (n.reported?.level ?? "NORMAL") !== "NORMAL";
}

// Spawn a REAL governed node (axor-core IntentLoop) on the proxy: it runs a
// governed session, uploads its trace, and stays live on the plane. Optionally
// switches the connection to adapter mode so Control shows the live node.
function SpawnGoverned({ switchToAdapter }: { switchToAdapter?: boolean }) {
  const qc = useQueryClient();
  const connect = useApp((s) => s.connect);
  const [err, setErr] = useState<string | null>(null);
  const spawnTree = useMutation({
    mutationFn: () => api.spawnGovernedTree(),
    onSuccess: async () => {
      setErr(null);
      if (switchToAdapter) connect("adapter");
      await qc.invalidateQueries({ queryKey: ["nodes"] });
      await qc.invalidateQueries({ queryKey: ["topology"] });
    },
    onError: (e: Error) => setErr(e.message),
  });
  const seedTree = useMutation({
    mutationFn: () => api.seedTreeRun(),
    onSuccess: async () => {
      setErr(null);
      if (switchToAdapter) connect("adapter");
      await qc.invalidateQueries({ queryKey: ["nodes"] });
      await qc.invalidateQueries({ queryKey: ["topology"] });
    },
    onError: (e: Error) => setErr(e.message),
  });
  const spawn = useMutation({
    mutationFn: () => api.spawnGoverned(),
    onSuccess: async () => {
      setErr(null);
      if (switchToAdapter) connect("adapter");
      await qc.invalidateQueries({ queryKey: ["nodes"] });
    },
    onError: (e: Error) => setErr(e.message),
  });
  return (
    <div className="mt-3" data-tour="spawn">
      <Tooltip content="Starts a real axor-core governed agent (an IntentLoop) on the proxy and connects it to the plane — so you can watch a live node heartbeat and obey your interventions without wiring your own.">
        <button
          onClick={() => spawn.mutate()}
          disabled={spawn.isPending}
          style={btn({ color: C.bg, background: C.green, border: `1px solid ${C.green}`, fontSize: 12, fontWeight: 700, padding: "8px 14px" })}
        >
          {spawn.isPending ? "spawning…" : "Spawn a governed demo node"}
        </button>
      </Tooltip>
      <Tooltip content="Runs a REAL 3-node governed tree (axor-core IntentLoops over the message bus): the scraper's web taint is carried up two delegation hops and the orchestrator's export is denied — containment, live.">
        <button
          onClick={() => spawnTree.mutate()}
          disabled={spawnTree.isPending}
          style={{ ...btn({ color: C.text, borderColor: C.steel, fontSize: 12, padding: "8px 14px" }), marginTop: 8 }}
        >
          {spawnTree.isPending ? "spawning tree…" : "Spawn a governed demo TREE"}
        </button>
      </Tooltip>
      {/* The canned tree, which until now had no way in: the backend route
          existed and nothing called it, so the lateral hop and the undeclared
          foreign peer — edge kinds a live spawn does not produce — were
          reachable only from the test suite. It also needs no proxy. */}
      <Tooltip content="Loads the canned 4-node tree straight into the plane — no proxy needed. It carries what a live spawn does not: a lateral hop between siblings, and a send to an UNDECLARED foreign peer, denied at the boundary and drawn as an opaque node.">
        <button
          onClick={() => seedTree.mutate()}
          disabled={seedTree.isPending}
          style={{ ...btn({ color: C.mut, fontSize: 11.5, padding: "7px 12px" }), marginTop: 8 }}
        >
          {seedTree.isPending ? "loading…" : "or load the canned tree (lateral + foreign peer)"}
        </button>
      </Tooltip>
      {err && (
        <div style={{ fontFamily: MONO, fontSize: 11, color: C.red, marginTop: 8 }}>
          {err} — is the proxy running with a backend URL?
        </div>
      )}
      {seedTree.isSuccess && (
        <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim, marginTop: 8 }}>
          canned tree loaded — 4 nodes plus the opaque foreign peer, on the map below.
        </div>
      )}
      {spawn.isSuccess && (
        <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim, marginTop: 8 }}>
          governed node live — it heartbeats to the plane; pause / stop / inject below reach it.
        </div>
      )}
    </div>
  );
}

export default function ControlTab({ focusNode }: { focusNode?: string }) {
  const { mode, testBench } = useApp((s) => s.connection);

  // Control is adapter-only by construction — the proxy has no handle on
  // internal topology (spec section 12). Grey it with the honest upsell.
  if (!isAdapter(mode)) {
    return (
      <div style={{ maxWidth: 640, margin: "0 auto" }}>
        <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 4px" }}>Operate the governed topology.</h1>
        <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, marginBottom: 20 }}>
          a live topology map, per-node interventions, cascade stop — the strongest upsell in the product.
        </div>
        <Locked need="adapter" title="live topology of your governed agents" >
          <div />
        </Locked>
        <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginTop: 16 }}>
          try it now — spawn a real governed node (axor-core) and watch it appear here live:
        </div>
        <SpawnGoverned switchToAdapter />
      </div>
    );
  }
  return <ControlBody focusNode={focusNode} testBench={testBench} />;
}

function ControlBody({ focusNode, testBench }: { focusNode?: string; testBench: boolean }) {
  const qc = useQueryClient();
  const nodes = useQuery({
    queryKey: ["nodes"],
    queryFn: api.nodes,
    refetchInterval: REFETCH_MS,
  });
  const [sel, setSel] = useState<string | null>(focusNode ?? null);
  const [lens, setLens] = useState<"list" | "graph">("list");
  const list = nodes.data ?? [];
  const topo = useQuery({
    queryKey: ["topology"],
    queryFn: api.topology,
    refetchInterval: REFETCH_MS,
    // Also when there is nothing to operate: `/v1/plane/nodes` lists nodes with
    // desired or reported state, i.e. nodes that heartbeat or have been
    // commanded — a traced tree's children are in the topology and not in that
    // list. Gating this query on the graph lens meant Control said "no governed
    // nodes connected yet" while it held the whole tree.
    enabled: lens === "graph" || list.length === 0,
  });
  const [more, setMore] = useState(false);
  const [cmdError, setCmdError] = useState<string | null>(null);
  const [budgetInput, setBudgetInput] = useState("");

  useEffect(() => {
    if (focusNode) setSel(focusNode);
  }, [focusNode]);

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

  const cascade = useMutation({
    // The version the signed command carries is the same one every other
    // command uses: the node's current desired version plus one. A signed
    // deployment refuses anything else with a 409 naming what it expected.
    mutationFn: ({ nodeId, version }: { nodeId: string; version: number }) =>
      api.cascadeStop(nodeId, version),
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

  const traced = topo.data?.nodes ?? [];
  if (list.length === 0) {
    return (
      <div style={{ maxWidth: 640, margin: "0 auto" }}>
        <div style={{ fontFamily: MONO, fontSize: 12.5, color: C.mut }}>
          {traced.length > 0
            ? <>No node is reporting to the plane yet, so there is nothing to pause
                or stop — but {traced.length} node{traced.length === 1 ? "" : "s"} have
                traced here, and the shape they describe is below.</>
            : <>No governed nodes connected yet. Point your axor-core adapter at the
                plane — or spawn a real governed demo node right now:</>}
        </div>
        <SpawnGoverned />
        {topo.data && traced.length > 0 && (
          <div className="mt-4">
            <TopologyGraph
              payload={topo.data}
              selected={sel}
              onSelect={(id) => setSel(sel === id ? null : id)}
            />
          </div>
        )}
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
      <Coach id="control" title="Control — operate governed agents live">
        Each row is a live governed agent (a node). Click one to open its panel:{" "}
        <span style={{ color: C.text }}>desired</span> is what you've commanded,{" "}
        <span style={{ color: C.text }}>reported</span> is what the node has applied —
        a gap means a command is still in flight. Pause, cap its budget, or stop it;
        interventions travel over the plane and the node obeys on its next turn.
      </Coach>
      <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 4px" }}>
        {anyHot ? <>One agent needs attention.</> : <>All agents healthy.</>}
      </h1>
      <div className="flex items-center gap-4" style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginBottom: 20 }}>
        <span>{list.length} node{list.length === 1 ? "" : "s"} · live</span>
        <span style={{ marginLeft: "auto", display: "flex", gap: 12 }}>
          {(["list", "graph"] as const).map((l) => (
            <button key={l} onClick={() => setLens(l)}
              style={{ background: "none", border: "none", cursor: "pointer",
                       fontFamily: MONO, fontSize: 12,
                       color: lens === l ? C.text : C.dim,
                       borderBottom: lens === l ? `2px solid ${C.steel}` : "none",
                       paddingBottom: 2 }}>
              {l}
            </button>
          ))}
        </span>
      </div>

      {lens === "graph" && topo.data && (
        <TopologyGraph
          payload={topo.data}
          selected={sel}
          onSelect={(id) => { setSel(sel === id ? null : id); setMore(false); setCmdError(null); }}
        />
      )}
      {lens === "graph" && topo.data && sel &&
        topo.data.nodes.find((n) => n.node_id === sel)?.kind === "peer" && (
        <PeerCard node={topo.data.nodes.find((n) => n.node_id === sel)!} />
      )}

      {/* Same tour anchor as SpawnGoverned: when nodes exist the tour spotlights
          the live topology instead of the (absent) spawn button. */}
      <div data-tour="spawn" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8,
                                      display: lens === "graph" ? "none" : "block" }}>
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

          {/* Budget cap — a first-class control (spec §15). Decrease-only over
              the plane: the adapter refuses any widening, so this can set an
              initial cap or tighten it, never raise it past the current one. */}
          <div className="flex items-center gap-2 mb-3" style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>
            <span>budget cap</span>
            <span style={{ color: C.text }}>
              {typeof desired?.state.budget_cap_calls === "number"
                ? `${desired.state.budget_cap_calls} calls`
                : "unlimited"}
            </span>
            <input
              type="number"
              min={0}
              value={budgetInput}
              onChange={(e) => setBudgetInput(e.target.value)}
              placeholder="set / lower"
              disabled={stopped || command.isPending}
              style={{ width: 92, background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: C.text, fontFamily: MONO, fontSize: 11, padding: "4px 7px", outline: "none" }}
            />
            <Tooltip content="Cap how many tool calls this agent may still make. You can set a cap or tighten it — raising it past the current cap is refused by the adapter (decrease-only).">
              <button
                onClick={() => {
                  const n = parseInt(budgetInput, 10);
                  if (Number.isNaN(n) || n < 0) { setCmdError("cap must be a non-negative integer"); return; }
                  const cur = typeof desired?.state.budget_cap_calls === "number" ? desired.state.budget_cap_calls : null;
                  if (cur != null && n > cur) { setCmdError(`can only lower the cap — ${n} > current ${cur} (the adapter refuses widening)`); return; }
                  send({ budget_cap_calls: n });
                  setBudgetInput("");
                }}
                disabled={stopped || command.isPending || budgetInput === ""}
                style={btn({ color: (stopped || budgetInput === "") ? C.dim : C.steel, borderColor: C.line, fontSize: 11, padding: "4px 10px" })}
              >
                <Gauge size={12} /> apply
              </button>
            </Tooltip>
          </div>

          <div className="flex gap-2">
            <Tooltip content={paused
              ? "Let the agent resume acting from where it paused."
              : "Hold the agent before its next turn — no work is lost; Resume continues it."}>
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
            </Tooltip>
            <button
              onClick={() => setMore(!more)}
              style={{ background: "none", border: "none", color: C.mut, fontFamily: MONO, fontSize: 12, cursor: "pointer" }}
            >
              more…
            </button>
          </div>

          {more && (
            <div className="flex gap-2 mt-2 flex-wrap">
              <Tooltip content="Stop this one agent for good — it finishes no further turns.">
                <button
                  onClick={() => send({ stopped: true })}
                  disabled={stopped || command.isPending}
                  style={btn({ color: stopped ? C.dim : C.mut, fontSize: 11, padding: "6px 10px" })}
                >
                  <Square size={12} /> Stop
                </button>
              </Tooltip>
              <Tooltip content="Stop this node AND every agent it spawned (its whole subtree) — the blast-radius kill switch.">
                <button
                  onClick={() => {
                    if (!node) return;
                    cascade.mutate({
                      nodeId: node.node_id,
                      version: (node.desired?.version ?? 0) + 1,
                    });
                  }}
                  disabled={cascade.isPending}
                  style={btn({ color: C.mut, fontSize: 11, padding: "6px 10px" })}
                >
                  <Square size={12} /> Cascade stop
                </button>
              </Tooltip>
              <Tooltip content="Ask the agent to drop its current plan and reconsider next turn. Your reason is recorded on the run.">
                <button
                  onClick={() => {
                    const id = `rp_${Date.now()}`;
                    const reason = window.prompt("Replan — reason for the operator record:");
                    if (reason == null) return;
                    send({ replan: { id, reason, operator: "op_ui" } });
                  }}
                  disabled={stopped || command.isPending}
                  style={btn({ color: stopped ? C.dim : C.mut, fontSize: 11, padding: "6px 10px", opacity: stopped ? 0.6 : 1 })}
                >
                  <GitBranch size={12} /> Replan
                </button>
              </Tooltip>
              <Tooltip content={!testBench
                ? "Test-bench only (enable in Settings). Injects text into the agent's next turn to probe recovery — so the run is marked intervened and excluded from scores."
                : "Insert text into the agent's next turn to probe how it recovers. The run is marked intervened and excluded from scores."}>
                <button
                  onClick={() => {
                    if (!node) return;
                    const text = window.prompt("Injection text (test-bench only) — inserted next turn:");
                    if (!text) return;
                    const reason = window.prompt("Reason (recorded):") ?? "";
                    send({
                      pending_injection: {
                        id: `inj_${Date.now()}`, text, reason, operator: "op_ui",
                      },
                    });
                  }}
                  disabled={!testBench || stopped || command.isPending}
                  style={btn({ color: (!testBench || stopped) ? C.dim : C.mut, fontSize: 11, padding: "6px 10px", cursor: (!testBench || stopped) ? "default" : "pointer", opacity: (!testBench || stopped) ? 0.6 : 1 })}
                >
                  <Syringe size={12} /> Inject next turn
                </button>
              </Tooltip>
            </div>
          )}

          {/* What is actually holding this node down, and what vouching for it
              would discharge. `covers` names fact ids (the kernel's contract),
              so attesting is a choice of which fact — not a gesture. */}
          <Coverage nodeId={node.node_id} refetchMs={REFETCH_MS} />

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
