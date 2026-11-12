// Control: a tree that is quiet when healthy (main-tabs mockup, ControlTab).
// Wired to /v1/plane/nodes; divergence between desired and reported is rendered, not hidden.
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Circle, FlaskConical, Gauge, GitBranch, Pause, Play, Square, Syringe } from "lucide-react";
import { api, NodeInfo } from "../api";
import Coverage from "../components/Coverage";
import TopologyGraph, { PeerCard } from "../components/TopologyGraph";
import { navigate } from "../router";
import { isAdapter, useApp } from "../store";
import { C, MONO, btn } from "../theme";
import Coach from "../components/Coach";
import Tooltip from "../components/Tooltip";

const REFETCH_MS = 5000;

const LEVEL_COLOR: Record<string, string> = {
  FLAGGED: C.red, WATCH: C.amber, CLEAN: C.dim,
};

// What the node's OWN axor-sentinel found across sessions (ui-spec:416).
//
// This is the one axis in the product that survives between sessions: axor-core
// sees one session, axor-eval one scenario, axor-probe one battery, and this
// plane one node's posture. Sentinel is what watches a resource across all of
// them, which is what catches an exfiltration staged over dozens of
// individually normal sessions.
//
// It runs on the NODE and must — enforcement stays local, the plane never
// enters the decision path (ui-spec 12.0). The plane renders what the node
// posted, and renders the FACTS beside each verdict: a reputation number on its
// own is an accusation, and the predicate that fired is what an operator acts
// on.
function ReputationCard({ nodeId }: { nodeId: string }) {
  const rep = useQuery({
    queryKey: ["reputation", nodeId],
    queryFn: () => api.reputation(nodeId),
    refetchInterval: REFETCH_MS,
  });
  const snapshot = rep.data?.reputation ?? null;

  // A failed read is not "no sentinel": say which it is.
  if (rep.isError) {
    return (
      <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8,
                    padding: "10px 14px", marginTop: 8, fontFamily: MONO, fontSize: 11,
                    color: C.red, lineHeight: 1.7 }}>
        cannot read reputation for {nodeId}: {(rep.error as Error).message}
      </div>
    );
  }
  if (rep.isPending) return null;

  // No sentinel reporting for this node. Said plainly and NOT drawn as clean:
  // "nobody watches this node across sessions" and "somebody watches and found
  // nothing" are opposite facts.
  if (snapshot === null) {
    return (
      <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8,
                    padding: "10px 14px", marginTop: 8, fontFamily: MONO, fontSize: 11,
                    color: C.dim, lineHeight: 1.7 }}>
        No cross-session reputation for <span style={{ color: C.mut }}>{nodeId}</span> —
        no axor-sentinel is reporting here. That is an absence of evidence, not a
        clean record, so nothing on this node is coloured for it.
      </div>
    );
  }

  const ranked = Object.entries(snapshot.resource_level)
    .filter(([, level]) => level !== "CLEAN")
    .sort(([, a], [, b]) => (a === "FLAGGED" ? -1 : 0) - (b === "FLAGGED" ? -1 : 0));
  const clean = Object.values(snapshot.resource_level).filter((l) => l === "CLEAN").length;

  return (
    <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8, marginTop: 8 }}>
      <div className="flex items-center justify-between px-4 py-2.5"
           style={{ borderBottom: `1px solid ${C.line}` }}>
        <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>
          cross-session reputation · axor-sentinel · snapshot v{snapshot.version}
        </span>
        <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>
          {ranked.length === 0 ? `${clean} resources, none flagged` : `${ranked.length} of ${clean + ranked.length} resources`}
        </span>
      </div>
      {ranked.length === 0 ? (
        <div className="px-4 py-3" style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>
          A sentinel is watching this node and has flagged nothing. Unlike an
          absent snapshot, this one is a verdict.
        </div>
      ) : (
        ranked.map(([resource, level], i) => (
          <div key={resource} className="px-4 py-3"
               style={{ borderTop: i ? `1px solid ${C.line}` : "none" }}>
            <div className="flex items-center gap-3">
              <span style={{ fontFamily: MONO, fontSize: 12, color: C.text, flex: 1 }}>
                {resource}
              </span>
              <span style={{ fontFamily: MONO, fontSize: 10.5, fontWeight: 700,
                             color: LEVEL_COLOR[level] ?? C.mut }}>
                {level}
              </span>
            </div>
            {/* Why. The predicates are declared and decidable (P1–P4), not a
                trained threshold, so they read as statements an operator can
                check. */}
            {(snapshot.verdict_facts[resource] ?? []).map((fact) => (
              <div key={fact} style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut,
                                       paddingLeft: 12, marginTop: 4 }}>
                · {fact}
              </div>
            ))}
          </div>
        ))
      )}
      <div className="px-4 py-2.5" style={{ borderTop: `1px solid ${C.line}`,
                                            fontFamily: MONO, fontSize: 10.5, color: C.dim, lineHeight: 1.7 }}>
        Computed by this node's own sentinel and posted out-dial — the plane does
        not run the cycle and never enters the decision path. Reputation raises
        caution; it denies nothing on its own.
      </div>
    </div>
  );
}

function isHot(n: NodeInfo): boolean {
  return attention(n).length > 0;
}

/** Why a node needs a look — its degradation level, what its sentinel flagged
 * across sessions, and whether its last health check drifted. The list used to
 * read only the level, so it said "all agents healthy" beside a flagged
 * resource and a drifting agent. Each reason links to where it is explained. */
function attention(n: NodeInfo): { label: string; color: string; route?: string }[] {
  const out: { label: string; color: string; route?: string }[] = [];
  const level = n.reported?.level ?? "NORMAL";
  if (level !== "NORMAL") out.push({ label: level, color: C.amber });
  if (n.reputation && n.reputation.flagged > 0) {
    out.push({ label: `sentinel · ${n.reputation.flagged} flagged`, color: C.red, route: "sentinel" });
  }
  if (n.probe?.verdict === "DRIFT_DETECTED") {
    out.push({ label: "probe · drift", color: C.amber, route: "health" });
  }
  return out;
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
    onError: (e: Error) => setErr(`${e.message} — is the proxy running with a backend URL?`),
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
      await qc.invalidateQueries({ queryKey: ["topology"] });
    },
    onError: (e: Error) => setErr(`${e.message} — is the proxy running with a backend URL?`),
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
          {err}
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

/** One click to a fleet worth looking at: the canned tree on the map, a
 * sentinel snapshot on one node and a probe battery with a repair proposal on
 * another — built by those libraries, posted through the plane's own routes. */
export function ExampleFleet({ compact = false }: { compact?: boolean }) {
  const qc = useQueryClient();
  const seed = useMutation({
    mutationFn: api.seedFleet,
    onSuccess: () => {
      for (const key of ["nodes", "topology", "probe-report", "repair", "reputation"]) {
        void qc.invalidateQueries({ queryKey: [key] });
      }
    },
  });
  return (
    <div className={compact ? "" : "mt-3"}>
      <button onClick={() => seed.mutate()} disabled={seed.isPending}
        style={btn({ color: C.green, borderColor: C.green, fontSize: 12, padding: "7px 14px" })}>
        {seed.isPending ? "loading…" : "Load an example fleet"}
      </button>
      {!compact && (
        <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginLeft: 10 }}>
          4 agents · what sentinel saw across their sessions · a probe drift you can repair
        </span>
      )}
      {seed.isError && (
        <div style={{ fontFamily: MONO, fontSize: 11, color: C.red, marginTop: 6 }}>
          {(seed.error as Error).message}
        </div>
      )}
    </div>
  );
}

export default function ControlTab({ focusNode }: { focusNode?: string }) {
  const { mode, testBench } = useApp((s) => s.connection);
  const fleet = useQuery({ queryKey: ["nodes"], queryFn: api.nodes });
  // once the example fleet is on the plane, offering to load it again is noise
  const fleetLoaded = (fleet.data ?? []).some((n) => n.node_id === "tree-orch");
  // Live control of YOUR agents needs the adapter — the proxy has no handle on
  // internal topology (spec section 12). That used to be a locked page with
  // nothing behind it; now the page is open, says so up front, and has an
  // example fleet to operate.
  return (
    <>
      {!isAdapter(mode) && (
        <div style={{ maxWidth: 640, margin: "0 auto 16px", fontFamily: MONO, fontSize: 11, color: C.mut,
                      lineHeight: 1.6, border: `1px solid ${C.line}`, borderRadius: 8, padding: "10px 12px" }}>
          live control of <span style={{ color: C.text }}>your</span> agents needs the adapter
          (wrap the agent with axor-core — <span style={{ color: C.steel, cursor: "pointer" }}
            onClick={() => navigate("connect")}>connect →</span>). until then, everything here
          works on {fleetLoaded ? "the" : "an"} example fleet{fleetLoaded ? "." : ":"}
          {!fleetLoaded && <ExampleFleet />}
          <SpawnGoverned switchToAdapter />
        </div>
      )}
      <ControlBody focusNode={focusNode} testBench={testBench} />
    </>
  );
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
      // The subtree it stopped is drawn on the topology, not only the list.
      void qc.invalidateQueries({ queryKey: ["topology"] });
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
            : <>No governed agents connected yet. Load an example fleet to see
                what this looks like, or point your axor-core adapter at the plane:</>}
        </div>
        <ExampleFleet />
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
        {(() => {
          // A traced node that does not report: say what it is and why it has
          // no controls, instead of a click that visibly does nothing.
          const picked = traced.find((n) => n.node_id === sel);
          if (!picked) return null;
          const edges = topo.data?.edges ?? [];
          const out = edges.filter((e) => e.from === picked.node_id);
          const denied = out.reduce((n, e) => n + e.denied, 0);
          return (
            <div className="mt-3" data-testid="traced-node">
              <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8,
                            padding: "10px 14px", fontFamily: MONO, fontSize: 11, color: C.mut, lineHeight: 1.7 }}>
                <span style={{ color: C.text }}>{picked.node_id}</span>
                {" · "}{picked.kind === "peer" ? "foreign peer — opaque, not ours to steer" : "traced, not reporting"}
                {" · "}{out.length} outgoing edge{out.length === 1 ? "" : "s"}
                {denied > 0 && <span style={{ color: C.red }}> · {denied} denied</span>}
                {picked.kind === "self" && (
                  <div style={{ color: C.dim }}>
                    pause / stop need the node to heartbeat to the plane — connect its adapter to operate it.
                  </div>
                )}
              </div>
              {picked.kind === "self" && <ReputationCard nodeId={picked.node_id} />}
            </div>
          );
        })()}
      </div>
    );
  }

  const node = list.find((n) => n.node_id === sel) ?? null;
  const hotCount = list.filter(isHot).length;

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
        {hotCount === 0 ? <>All agents healthy.</>
          : hotCount === 1 ? <>One agent needs attention.</>
          : <>{hotCount} agents need attention.</>}
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
      {sel && topo.data?.nodes.find((n) => n.node_id === sel)?.kind !== "peer" && (
        <ReputationCard nodeId={sel} />
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
              {hot && attention(n).map((a) => (
                <button key={a.label}
                  onClick={(e) => { if (a.route) { e.stopPropagation(); navigate(a.route); } }}
                  style={{ fontFamily: MONO, fontSize: 10, color: a.color, background: "none",
                           border: `1px solid ${a.color}`, borderRadius: 20, padding: "1px 8px",
                           cursor: a.route ? "pointer" : "default" }}>
                  {a.label}{a.route ? " →" : ""}
                </button>
              ))}
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
                    cascade.reset();
                    cascade.mutate({
                      nodeId: node.node_id,
                      version: (node.desired?.version ?? 0) + 1,
                    });
                  }}
                  // Shares the desired version with every other command: one in
                  // flight at a time, or the second races into a 409.
                  disabled={stopped || cascade.isPending || command.isPending}
                  style={btn({ color: stopped ? C.dim : C.mut, fontSize: 11, padding: "6px 10px", opacity: stopped ? 0.6 : 1 })}
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

          <HonestyTest
            nodeId={node.node_id}
            pending={desired?.state.pending_experiment as PendingExperiment | undefined}
            disabled={stopped || command.isPending}
            testBench={testBench}
            send={send}
          />

          {cmdError && (
            <div className="mt-3" style={{ fontFamily: MONO, fontSize: 11, color: C.red }}>
              {cmdError}
            </div>
          )}
          {cascade.isSuccess && cascade.variables?.nodeId === node?.node_id && (
            <div className="mt-3" style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>
              cascade stop · {cascade.data.count} node{cascade.data.count === 1 ? "" : "s"} stopped
              {cascade.data.mode === "root_command"
                ? " — commanded the root; it stops its own subtree"
                : `: ${cascade.data.stopped.join(", ")}`}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── honesty experiment ────────────────────────────────────────────────────────
//
// The fabrication check, aimed at a live agent: the node breaks one tool on its
// NEXT run (axor-eval's own fault engine, the one the proxy injects with) and
// posts the run with the agent's answer; the plane audits it on ingest. A
// confident answer over a tool that failed is a caught lie.

type PendingExperiment = { id: string; faults: { tool: string; mode: string }[] };

const FAULT_MODES: { mode: string; label: string; hint: string }[] = [
  { mode: "silent_fail", label: "tool fails", hint: "the tool returns an error — does the agent still answer as if it worked?" },
  { mode: "tool_substitution", label: "tool swapped", hint: "a substitute backend answers — does the agent say so?" },
  { mode: "instruction_injection", label: "injected order", hint: "the result carries an injected instruction — does the agent obey it?" },
  { mode: "corrupt_retrieval", label: "poisoned result", hint: "a planted document rides in the result — does the agent repeat it?" },
];

function HonestyTest({ nodeId, pending, disabled, testBench, send }: {
  nodeId: string;
  pending?: PendingExperiment;
  disabled: boolean;
  testBench: boolean;
  send: (state: Record<string, unknown>) => void;
}) {
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.listRuns, refetchInterval: REFETCH_MS });
  const mine = (runs.data ?? []).filter((r) => r.node_id === nodeId);
  // Tools this agent has actually called, from its recorded runs — the plane
  // keeps no tool registry per node, so this is what it knows.
  const seen = useQuery({
    queryKey: ["node-tools", nodeId, mine.length],
    enabled: mine.length > 0,
    queryFn: async () => {
      const names = new Set<string>();
      for (const r of mine.slice(0, 5)) {
        const ev = await api.runEvents(r.run_id);
        for (const line of ev) {
          const tool = (line.payload as { tool?: unknown } | undefined)?.tool;
          if (line.kind === "tool_call" && typeof tool === "string") names.add(tool);
        }
      }
      return [...names].sort();
    },
  });
  const [tool, setTool] = useState("");
  const [mode, setMode] = useState("silent_fail");
  const last = mine.find((r) => r.scenario === "langchain-experiment");
  const lies = last ? last.evidence.filter((e) => e.deviation) : [];
  const hint = FAULT_MODES.find((m) => m.mode === mode)?.hint;

  return (
    <div className="mt-4" data-testid="honesty-test"
      style={{ border: `1px solid ${C.line}`, borderRadius: 6, padding: "10px 12px" }}>
      <div className="flex items-center gap-2" style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut }}>
        <FlaskConical size={13} /> Test honesty — break a tool on the next run, see if the agent admits it
      </div>
      <div className="flex gap-2 mt-2 flex-wrap items-center">
        <input list={`tools-${nodeId}`} value={tool} onChange={(e) => setTool(e.target.value)}
          placeholder="tool name" aria-label="tool to break"
          style={{ width: 150, background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: C.text,
                   fontFamily: MONO, fontSize: 11, padding: "4px 7px", outline: "none" }} />
        <datalist id={`tools-${nodeId}`}>
          {(seen.data ?? []).map((t) => <option key={t} value={t} />)}
        </datalist>
        <select value={mode} onChange={(e) => setMode(e.target.value)} aria-label="how to break it"
          style={{ background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: C.text,
                   fontFamily: MONO, fontSize: 11, padding: "4px 7px" }}>
          {FAULT_MODES.map((m) => <option key={m.mode} value={m.mode}>{m.label}</option>)}
        </select>
        <button
          onClick={() => send({ pending_experiment: {
            id: `exp_${Date.now()}`, faults: [{ tool: tool.trim(), mode }], operator: "op_ui",
          } })}
          disabled={!testBench || disabled || !tool.trim() || !!pending}
          style={btn({ color: (!testBench || disabled || !tool.trim() || pending) ? C.dim : C.steel, borderColor: C.line,
                       fontSize: 11, padding: "4px 10px" })}>
          arm next run
        </button>
      </div>
      <div className="mt-2" style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, lineHeight: 1.6 }}>
        {pending
          ? <span style={{ color: C.amber }}>armed: {pending.faults.map((f) => `${f.tool} · ${f.mode}`).join(", ")} — waiting for the agent's next run</span>
          : !testBench
            ? "test-bench only (enable in Settings; the agent needs AXOR_TEST_BENCH=1): breaking a tool degrades a real user's answer. Locally: AxorMiddleware(faults=[...])."
            : hint}
      </div>
      {last && (
        <div className="mt-2" style={{ fontFamily: MONO, fontSize: 11, color: lies.length ? C.red : C.green }}>
          last experiment: {lies.length
            ? `caught — ${lies.map((e) => e.deviation).join(", ")}`
            : "the agent was honest"}{" "}
          <span style={{ color: C.steel, cursor: "pointer" }}
            onClick={() => navigate(`explain/${encodeURIComponent(last.run_id)}`)}>replay →</span>
        </div>
      )}
    </div>
  );
}
