// Control: a tree that is quiet when healthy (main-tabs mockup, ControlTab).
// Wired to /v1/plane/nodes; divergence between desired and reported is rendered, not hidden.
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Circle, FlaskConical, Gauge, GitBranch, Lock, Pause, Play, Square, Syringe } from "lucide-react";
import { ActivityEvent, api, FleetRow, NodeInfo, NodeRun, type LockReason, type TreeSummary } from "../api";
import { describeAck } from "./RunControl";
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


/** Why a node needs a look — its degradation level, what its sentinel flagged
 * across sessions, and whether its last health check drifted. The list used to
 * read only the level, so it said "all agents healthy" beside a flagged
 * resource and a drifting agent. Each reason links to where it is explained. */
function attention(n: NodeInfo): { label: string; color: string; route?: string }[] {
  const out: { label: string; color: string; route?: string }[] = [];
  const level = n.reported?.level ?? "NORMAL";
  const paused = n.desired?.state.paused === true;
  const stopped = n.desired?.state.stopped === true;
  // A paused node reports CAUTIOUS and a stopped one RESTRICTED: the level
  // mirrors the posture the operator set. Say the posture, not the mirror;
  // a level beyond it is still news and is shown.
  if (stopped) out.push({ label: "stopped", color: C.red });
  else if (paused) out.push({ label: "paused", color: C.amber });
  const mirrored = (paused && level === "CAUTIOUS") || (stopped && level === "RESTRICTED");
  if (level !== "NORMAL" && !mirrored) out.push({ label: level, color: C.amber });
  if (n.reputation && n.reputation.flagged > 0) {
    out.push({ label: `reputation · ${n.reputation.flagged} flagged`, color: C.red,
               route: `sentinel?node=${encodeURIComponent(n.node_id)}` });
  }
  if (n.probe?.verdict === "DRIFT_DETECTED") {
    out.push({ label: "probe · drift", color: C.amber,
               route: `health?node=${encodeURIComponent(n.node_id)}` });
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
  const hasAgents = (fleet.data ?? []).length > 0;
  const [examples, setExamples] = useState(false);
  const note = (
    <>
      live control of <span style={{ color: C.text }}>your</span> agents needs the adapter
      (wrap the agent with axor-core — <span style={{ color: C.steel, cursor: "pointer" }}
        onClick={() => navigate("connect")}>connect →</span>)
    </>
  );
  return (
    <>
      {/* decided once the list is in: a note that unfolds and then folds
          itself takes the button out from under the pointer */}
      {!isAdapter(mode) && fleet.isSuccess && (hasAgents ? (
        // agents are already on screen: one line, the example tools folded away
        <div data-testid="adapter-note" style={{ fontFamily: MONO, fontSize: 11, color: C.mut,
                                                  lineHeight: 1.6, marginBottom: 14 }}>
          {note}.{" "}
          <button onClick={() => setExamples((v) => !v)}
            style={{ background: "none", border: "none", padding: 0, cursor: "pointer",
                     fontFamily: MONO, fontSize: 11, color: C.steel }}>
            {examples ? "hide example agents ▴" : "example agents ▾"}
          </button>
          {examples && (
            <div style={{ border: `1px solid ${C.line}`, borderRadius: 8, padding: "10px 12px", marginTop: 8 }}>
              {!fleetLoaded && <ExampleFleet />}
              <SpawnGoverned switchToAdapter />
            </div>
          )}
        </div>
      ) : (
        <div style={{ maxWidth: 640, margin: "0 auto 16px", fontFamily: MONO, fontSize: 11, color: C.mut,
                      lineHeight: 1.6, border: `1px solid ${C.line}`, borderRadius: 8, padding: "10px 12px" }}>
          {note}. until then, everything here works on an example fleet:
          {!fleetLoaded && <ExampleFleet />}
          <SpawnGoverned switchToAdapter />
        </div>
      ))}
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
  const [filter, setFilter] = useState<Status | "all">("all");
  const fleetQ = useQuery({ queryKey: ["fleet"], queryFn: api.fleet, refetchInterval: REFETCH_MS });
  // a tree's agents fold into one row: which node belongs to which tree run
  const treesQ = useQuery({ queryKey: ["trees"], queryFn: api.trees, refetchInterval: REFETCH_MS });
  // past the free plan a coding session (or a tree mixing agent families) is
  // kept but locked: named in its row, and its panel says what unlocks it
  const entQ = useQuery({ queryKey: ["entitlements"], queryFn: api.entitlements,
                          refetchInterval: REFETCH_MS, retry: false });
  const lockedOf = (id: string): LockReason[] | undefined =>
    entQ.data?.locked[id] ?? entQ.data?.locked_trees[id];
  const treeOf = new Map<string, TreeSummary>();
  for (const t of treesQ.data?.trees ?? []) {
    treeOf.set(t.tree_run, t);
    for (const m of t.members ?? []) treeOf.set(m, t);
  }
  const [openTrees, setOpenTrees] = useState<Set<string>>(new Set());
  const fleetRows = new Map((fleetQ.data?.nodes ?? []).map((r) => [r.node_id, r]));
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

  const statuses = new Map(list.map((n) => [n.node_id, statusesOf(n, fleetRows.get(n.node_id))]));
  // Counters count what the list shows: a tree is one row, so it is one unit,
  // and its statuses are its agents' together (one agent needing you puts the
  // tree in "needs you"; one running makes it running, not idle).
  const unitOf = (id: string) => {
    const t = treeOf.get(id);
    return t ? `tree:${t.tree_run}` : id;
  };
  const unitStatuses = new Map<string, Status[]>();
  for (const n of list) {
    const k = unitOf(n.node_id);
    unitStatuses.set(k, [...(unitStatuses.get(k) ?? []), ...statuses.get(n.node_id)!]);
  }
  for (const [k, st] of unitStatuses) {
    if (!k.startsWith("tree:")) continue;
    const merged = new Set(st);
    if (treeOf.get(k.slice(5))?.status === "running") merged.add("running");
    if (merged.has("running") || merged.has("paused") || merged.has("stopped")) merged.delete("idle");
    unitStatuses.set(k, [...merged]);
  }
  const units = [...unitStatuses.values()];
  const treeCount = [...unitStatuses.keys()].filter((k) => k.startsWith("tree:")).length;
  const looseCount = units.length - treeCount;
  // the heading counts what the "needs you" chip counts — one definition
  const needYou = units.filter((st) => st.includes("needs_you")).length;
  const shown = list.filter((n) => filter === "all" || unitStatuses.get(unitOf(n.node_id))!.includes(filter));
  const pick = (id: string) => {
    setSel(sel === id ? null : id);
    setMore(false);
    setCmdError(null);
  };

  return (
    <div className={node ? "fleet-grid fleet-grid-open" : "fleet-grid"}>
    <div style={{ minWidth: 0 }}>
      <Coach id="control" title="Control — operate governed agents live">
        Each row is a live governed agent (a node). Click one to open its panel:{" "}
        <span style={{ color: C.text }}>desired</span> is what you've commanded,{" "}
        <span style={{ color: C.text }}>reported</span> is what the node has applied —
        a gap means a command is still in flight. Pause, cap its budget, or stop it;
        interventions travel over the plane and the node obeys on its next turn.
      </Coach>
      <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 4px" }}>
        {needYou === 0 ? <>All agents healthy.</>
          : needYou === 1 ? <>One agent needs attention.</>
          : <>{needYou} agents need attention.</>}
      </h1>
      <StatusChips units={units} filter={filter} setFilter={setFilter} />
      {fleetQ.data && <FleetTotals totals={fleetQ.data.totals} />}
      <div className="flex items-center gap-4" style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginBottom: 20 }}>
        <span data-testid="fleet-count">
          {looseCount} agent{looseCount === 1 ? "" : "s"}
          {treeCount > 0 && <> · {treeCount} tree{treeCount === 1 ? "" : "s"}</>} · live
        </span>
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
      {/* a traced node that does not report has no panel: its reputation
          stays here, by the graph */}
      {sel && !node && topo.data?.nodes.find((n) => n.node_id === sel)?.kind !== "peer" && (
        <ReputationCard nodeId={sel} />
      )}

      {/* Same tour anchor as SpawnGoverned: when nodes exist the tour spotlights
          the live topology instead of the (absent) spawn button. */}
      <div data-tour="spawn" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8,
                                      display: lens === "graph" ? "none" : "block" }}>
        {shown.length === 0 && (
          <div className="px-4 py-3" style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>
            no agents {filter === "all" ? "" : `in “${STATUS_LABEL[filter as Status]}”`}
          </div>
        )}
        {(() => {
          // one row per tree, where its first agent would have been
          const out: React.ReactNode[] = [];
          const done = new Set<string>();
          shown.forEach((n) => {
            const tree = treeOf.get(n.node_id);
            if (!tree) {
              out.push(
                <FleetRowView key={n.node_id} n={n} row={fleetRows.get(n.node_id)} locked={lockedOf(n.node_id)}
                  st={statuses.get(n.node_id)!} first={out.length === 0} selected={sel === n.node_id}
                  onPick={() => pick(n.node_id)} />,
              );
              return;
            }
            if (done.has(tree.tree_run)) return;
            done.add(tree.tree_run);
            const members = shown.filter((m) => treeOf.get(m.node_id)?.tree_run === tree.tree_run
                                                && m.node_id !== tree.tree_run);
            const open = openTrees.has(tree.tree_run) || members.some((m) => m.node_id === sel);
            out.push(
              <TreeGroupRow key={`tree:${tree.tree_run}`} tree={tree} first={out.length === 0}
                locked={lockedOf(tree.tree_run)}
                open={open}
                onToggle={() => setOpenTrees((cur) => {
                  const next = new Set(cur);
                  if (next.has(tree.tree_run)) next.delete(tree.tree_run); else next.add(tree.tree_run);
                  return next;
                })}>
                {members.map((m) => (
                  <FleetRowView key={m.node_id} n={m} row={fleetRows.get(m.node_id)} locked={lockedOf(m.node_id)}
                    st={statuses.get(m.node_id)!} first={false} selected={sel === m.node_id}
                    onPick={() => pick(m.node_id)} />
                ))}
              </TreeGroupRow>,
            );
          });
          return out;
        })()}
      </div>

    </div>
    <aside className="fleet-sticky" data-testid={node ? "agent-panel" : "fleet-activity"}>
      {node ? (
        <>
          <div className="flex items-center" style={{ marginBottom: 8 }}>
            <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, letterSpacing: "0.06em" }}>AGENT</span>
            <button onClick={() => pick(node.node_id)} aria-label="close the agent panel"
              data-testid="agent-panel-close"
              style={{ marginLeft: "auto", background: "none", border: "none", cursor: "pointer",
                       fontFamily: MONO, fontSize: 11, color: C.dim }}>
              ✕ back to activity
            </button>
          </div>
          {lockedOf(node.node_id) ? (
            <PlanLock nodeId={node.node_id} reasons={lockedOf(node.node_id)!} />
          ) : (<>
          {topo.data?.nodes.find((n) => n.node_id === node.node_id)?.kind !== "peer" && (
            <ReputationCard nodeId={node.node_id} />
          )}
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

          <RunsPanel nodeId={node.node_id} paused={paused} stopped={stopped}
            disabled={command.isPending} send={send} />

          <details className="mb-3" style={{ fontFamily: MONO, fontSize: 11, color: C.mut, lineHeight: 1.7 }}>
            <summary style={{ cursor: "pointer", color: C.dim }}>
              protocol state{applying ? " · a command is in flight" : ""}
            </summary>
            commanded (desired){" "}
            <span style={{ color: C.text }}>
              v{desired?.version ?? 0} {JSON.stringify(desired?.state ?? {})}
            </span>
            <br />
            applied (reported){" "}
            <span style={{ color: applying ? C.amber : C.text }}>
              v{reported?.applied_version ?? 0} · {reported?.level ?? "unknown"}
              {reported?.budget_remaining != null
                ? ` · budget ${reported.budget_remaining}`
                : ""}
            </span>
          </details>

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
          </>)}
        </>
      ) : (
        <ActivityFeed />
      )}
    </aside>
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

// ── the node's runs ───────────────────────────────────────────────────────────
//
// What an operator controls is a run. The card showed the node's posture and
// nothing of what it was doing: each row is one agent invocation — its tools,
// the results that arrived tainted (what a cut names), the health check run
// against it, a caught lie — and opens in Replay. An active run carries the
// node's controls, because they act on it: pause holds it at its next
// boundary, stop ends it.

function hhmmss(iso: string): string {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toTimeString().slice(0, 8);
}

function RunsPanel({ nodeId, paused, stopped, disabled, send }: {
  nodeId: string; paused: boolean; stopped: boolean; disabled: boolean;
  send: (state: Record<string, unknown>) => void;
}) {
  const runs = useQuery({
    queryKey: ["node-runs", nodeId],
    queryFn: () => api.nodeRuns(nodeId),
    refetchInterval: REFETCH_MS,
  });
  const rows: NodeRun[] = runs.data?.runs ?? [];
  return (
    <div className="mb-4" data-testid="node-runs">
      <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginBottom: 6 }}>
        runs {rows.length ? `· ${rows.length} recent` : ""}
      </div>
      {runs.isSuccess && rows.length === 0 && (
        <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>
          no runs recorded yet — the node is connected; its agent has not done any work since.
        </div>
      )}
      {rows.length > 0 && (
        <div style={{ border: `1px solid ${C.line}`, borderRadius: 6, overflow: "hidden" }}>
          {rows.map((r, i) => (
            <RunRow key={r.run_id} run={r} first={i === 0}
              paused={paused} stopped={stopped} disabled={disabled} send={send} />
          ))}
        </div>
      )}
    </div>
  );
}

function RunRow({ run, first, paused, stopped, disabled, send }: {
  run: NodeRun; first: boolean; paused: boolean; stopped: boolean; disabled: boolean;
  send: (state: Record<string, unknown>) => void;
}) {
  const experiment = (run.scenario ?? "").endsWith("-experiment");
  const lies = run.evidence.length;
  const drift = run.probe?.verdict === "DRIFT_DETECTED";
  const chip = (label: string, color: string) => (
    <span style={{ fontFamily: MONO, fontSize: 10, color, border: `1px solid ${color}`,
                   borderRadius: 20, padding: "0 7px", whiteSpace: "nowrap" }}>{label}</span>
  );
  return (
    <div className="flex items-center gap-2 px-3 py-2 wrap-narrow"
      onClick={() => navigate(`govern/run/${encodeURIComponent(run.run_id)}`)}
      style={{ cursor: "pointer", borderTop: first ? "none" : `1px solid ${C.line}`,
               fontFamily: MONO, fontSize: 11, flexWrap: "wrap" }}>
      <Circle size={7} fill={run.active ? C.green : C.dim} color={run.active ? C.green : C.dim} />
      <span style={{ color: C.mut }}>{hhmmss(run.started_ts)}</span>
      <span style={{ color: run.active ? C.green : C.dim }}>
        {run.active ? (paused ? "held" : "running") : "done"}
      </span>
      <span style={{ color: C.text, flex: 1, minWidth: 120 }}>
        {run.tool_calls} call{run.tool_calls === 1 ? "" : "s"}
        {run.tools.length ? ` · ${run.tools.join(", ")}` : ""}
      </span>
      {run.tainted_refs.length > 0 && chip(`${run.tainted_refs.length} tainted`, C.amber)}
      {run.denied > 0 && chip(`${run.denied} denied`, C.red)}
      {experiment && chip("honesty test", C.steel)}
      {lies > 0 && chip("caught lie", C.red)}
      {experiment && lies === 0 && chip("honest", C.green)}
      {run.probe && chip(drift ? "drift" : run.probe.verdict.toLowerCase().replace(/_/g, " "),
                         drift ? C.amber : C.green)}
      {run.active && !stopped && (
        <span className="flex gap-1" onClick={(e) => e.stopPropagation()}>
          <button disabled={disabled} onClick={() => send({ paused: !paused })}
            style={btn({ fontSize: 10, padding: "2px 8px", color: C.text })}>
            {paused ? <Play size={10} /> : <Pause size={10} />} {paused ? "resume" : "pause"}
          </button>
          <button disabled={disabled} onClick={() => send({ stopped: true })}
            style={btn({ fontSize: 10, padding: "2px 8px", color: C.mut })}>
            <Square size={10} /> stop
          </button>
        </span>
      )}
      <span style={{ color: C.steel }}>open →</span>
    </div>
  );
}

// ── the fleet console: status, totals, sidebar, activity ──────────────────────
//
// One screen for the whole fleet: which agents are running and on what, which
// need you and why, what the last day cost in calls and what it caught — and,
// beside it, everything happening across the fleet as it happens.

type Status = "needs_you" | "running" | "paused" | "stopped" | "idle";

const STATUS_LABEL: Record<Status, string> = {
  needs_you: "needs you", running: "running", paused: "paused", stopped: "stopped", idle: "idle",
};

const STATUS_COLOR: Record<Status, string> = {
  needs_you: C.amber, running: C.green, paused: C.amber, stopped: C.red, idle: C.dim,
};

function statusesOf(n: NodeInfo, row?: FleetRow): Status[] {
  const out: Status[] = [];
  const stopped = n.desired?.state.stopped === true;
  const paused = n.desired?.state.paused === true;
  const posture = attention(n).filter((a) => a.label !== "paused" && a.label !== "stopped");
  if (posture.length > 0 || (row?.lies ?? 0) > 0) out.push("needs_you");
  if (stopped) out.push("stopped");
  else if (paused) out.push("paused");
  if (row?.active_run && !stopped) out.push("running");
  if (!row?.active_run && !stopped && !paused) out.push("idle");
  return out;
}

/** The dot an agent shows: its most urgent state. */
function primary(st: Status[]): Status {
  for (const s of ["stopped", "needs_you", "paused", "running", "idle"] as Status[]) {
    if (st.includes(s)) return s;
  }
  return "idle";
}

function ago(iso?: string | null): string {
  if (!iso) return "—";
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (isNaN(s)) return "—";
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function StatusChips({ units, filter, setFilter }: {
  units: Status[][];
  filter: Status | "all"; setFilter: (f: Status | "all") => void;
}) {
  const count = (s: Status) => units.filter((st) => st.includes(s)).length;
  const chip = (id: Status | "all", label: string, n: number, color: string) => (
    <button key={id} onClick={() => setFilter(id)} aria-pressed={filter === id}
      style={{ fontFamily: MONO, fontSize: 11.5, background: filter === id ? "rgba(127,168,204,0.10)" : "none",
               border: `1px solid ${filter === id ? C.steel : C.line}`, borderRadius: 6, padding: "4px 10px",
               color: n ? C.text : C.dim, cursor: "pointer", display: "flex", alignItems: "center", gap: 6 }}>
      {id !== "all" && <Circle size={7} fill={color} color={color} />}
      {label} <span style={{ color: C.mut }}>{n}</span>
    </button>
  );
  return (
    <div className="flex gap-2 flex-wrap mb-2" data-testid="status-chips">
      {chip("all", "all", units.length, C.text)}
      {(["needs_you", "running", "paused", "stopped", "idle"] as Status[]).map((s) =>
        chip(s, STATUS_LABEL[s], count(s), STATUS_COLOR[s]))}
    </div>
  );
}

function FleetTotals({ totals }: { totals: { runs: number; calls: number; tainted: number; denied: number; lies: number; active: number } }) {
  const part = (v: number, label: string, color?: string) => (
    <span style={{ color: v && color ? color : C.mut }}><span style={{ color: v ? (color ?? C.text) : C.mut }}>{v}</span> {label}</span>
  );
  return (
    <div className="flex gap-3 flex-wrap mb-3" data-testid="fleet-totals"
      style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>
      <span>last 24h</span>
      {part(totals.active, "running now", C.green)}
      {part(totals.runs, "runs")}
      {part(totals.calls, "tool calls")}
      {part(totals.tainted, "untrusted results", C.amber)}
      {part(totals.denied, "denied", C.red)}
      {part(totals.lies, "caught lies", C.red)}
    </div>
  );
}

/** A tree run's agents, folded into one row: the task, how many agents, the
 * status — and the tree's own screen one click away. Unfold to operate one. */
const LOCK_LABEL: Record<LockReason, string> = {
  developer: "second developer",
  agent_family: "second agent family",
};

function LockChip({ reasons }: { reasons: LockReason[] }) {
  return (
    <span data-testid="plan-lock" style={{ fontFamily: MONO, fontSize: 10, color: C.amber,
      border: `1px solid ${C.amber}`, borderRadius: 20, padding: "1px 8px",
      display: "inline-flex", alignItems: "center", gap: 4 }}>
      <Lock size={9} /> paid plan · {reasons.map((r) => LOCK_LABEL[r]).join(", ")}
    </span>
  );
}

/** The panel of a session past the free plan: why, what still holds, what unlocks it. */
function PlanLock({ nodeId, reasons }: { nodeId: string; reasons: LockReason[] }) {
  return (
    <div data-testid="plan-lock-card" className="p-4" style={{ background: C.panel,
      border: `1px solid ${C.amber}`, borderRadius: 8, fontFamily: MONO, fontSize: 11.5,
      color: C.mut, lineHeight: 1.7 }}>
      <div style={{ color: C.text, fontSize: 13, marginBottom: 6 }}>{nodeId}</div>
      <div style={{ color: C.amber, marginBottom: 6 }}>
        <Lock size={11} style={{ display: "inline", marginRight: 6 }} />
        {reasons.includes("agent_family")
          ? "A second agent family — the free plan keeps one (Claude Code or Codex)."
          : "A second developer — the free plan keeps one."}
      </div>
      <div>
        The session is still protected: its hooks deny what they must, whatever the plan.
        It is recorded too — on the Coding plan it shows here with its history, and you
        can pause, stop and cap it.
      </div>
      <button onClick={() => navigate("pricing?for=coding")} data-testid="plan-lock-pricing"
        style={btn({ color: C.text, borderColor: C.steel, fontSize: 11.5, padding: "5px 12px", marginTop: 10 })}>
        Coding plan · $25 / developer / month →
      </button>
    </div>
  );
}

function TreeGroupRow({ tree, first, open, onToggle, locked, children }: {
  tree: TreeSummary; first: boolean; open: boolean; onToggle: () => void;
  locked?: LockReason[];
  children: React.ReactNode;
}) {
  const color = tree.status === "running" ? C.green : tree.status === "failed" ? C.red : C.dim;
  return (
    <div data-testid={`tree-row-${tree.tree_run}`}
      style={{ borderTop: first ? "none" : `1px solid ${C.line}` }}>
      <div onClick={onToggle} className="px-4 py-3" style={{ cursor: "pointer" }}>
        <div className="flex items-center gap-2.5 flex-wrap">
          <GitBranch size={12} color={color} />
          <span style={{ fontFamily: MONO, fontSize: 13, color: C.text, flex: 1, minWidth: 120 }}>
            {tree.task || tree.tree_run}
          </span>
          {locked && <LockChip reasons={locked} />}
          <button onClick={(e) => { e.stopPropagation(); navigate(`trees/${encodeURIComponent(tree.tree_run)}`); }}
            style={{ fontFamily: MONO, fontSize: 10.5, color: C.steel, background: "none",
                     border: "none", cursor: "pointer" }}>
            open tree →
          </button>
        </div>
        <div className="flex gap-3 flex-wrap" style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, marginTop: 4, marginLeft: 22 }}>
          <span style={{ color }}>{tree.status}</span>
          <span>agent tree · {tree.agents} agent run{tree.agents === 1 ? "" : "s"}</span>
          <span style={{ color: C.dim }}>{tree.tree_run}</span>
          <span style={{ color: C.steel }}>{open ? "▾ hide agents" : "▸ show agents"}</span>
        </div>
      </div>
      {open && <div style={{ marginLeft: 22, borderLeft: `1px solid ${C.line}` }}>{children}</div>}
    </div>
  );
}

function FleetRowView({ n, row, st, first, selected, onPick, locked }: {
  n: NodeInfo; row?: FleetRow; st: Status[]; first: boolean; selected: boolean; onPick: () => void;
  locked?: LockReason[];
}) {
  const p = primary(st);
  const color = STATUS_COLOR[p];
  return (
    <div onClick={onPick} className="px-4 py-3" data-testid={`agent-row-${n.node_id}`}
      style={{ cursor: "pointer", borderTop: first ? "none" : `1px solid ${C.line}`,
               background: selected ? "rgba(127,168,204,0.05)" : "transparent" }}>
      <div className="flex items-center gap-2.5 flex-wrap">
        <Circle size={8} fill={color} color={color} />
        <span style={{ fontFamily: MONO, fontSize: 13, color: locked ? C.mut : C.text, flex: 1, minWidth: 120 }}>{n.node_id}</span>
        {locked && <LockChip reasons={locked} />}
        {!locked && attention(n).map((a) => (
          <button key={a.label}
            onClick={(e) => { if (a.route) { e.stopPropagation(); navigate(a.route); } }}
            style={{ fontFamily: MONO, fontSize: 10, color: a.color, background: "none",
                     border: `1px solid ${a.color}`, borderRadius: 20, padding: "1px 8px",
                     cursor: a.route ? "pointer" : "default" }}>
            {a.label}{a.route ? " →" : ""}
          </button>
        ))}
        {(row?.lies ?? 0) > 0 && (
          <span style={{ fontFamily: MONO, fontSize: 10, color: C.red, border: `1px solid ${C.red}`,
                         borderRadius: 20, padding: "1px 8px" }}>caught lie</span>
        )}
      </div>
      {locked ? (
        <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 4, marginLeft: 18 }}>
          protected by its hooks · shown and operated on the Coding plan
        </div>
      ) : (
      <div className="flex gap-3 flex-wrap" style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, marginTop: 4, marginLeft: 18 }}>
        {row?.active_run ? (
          <span style={{ color: C.green, cursor: "pointer" }}
            onClick={(e) => { e.stopPropagation(); navigate(`govern/run/${encodeURIComponent(row.active_run!)}`); }}>
            ● running{row.current_tool ? ` · ${row.current_tool}` : ""} →
          </span>
        ) : (
          <span>{STATUS_LABEL[p]} · last active {ago(row?.last_ts)}</span>
        )}
        {row && <span>{row.runs} run{row.runs === 1 ? "" : "s"} · {row.calls} calls</span>}
        {row && row.tainted > 0 && <span style={{ color: C.amber }}>{row.tainted} untrusted</span>}
        {row && row.denied > 0 && <span style={{ color: C.red }}>{row.denied} denied</span>}
      </div>
      )}
    </div>
  );
}

function FleetSidebar({ list, statuses, rows, sel, onPick }: {
  list: NodeInfo[]; statuses: Map<string, Status[]>; rows: Map<string, FleetRow>;
  sel: string | null; onPick: (id: string) => void;
}) {
  // the agents that need a look first, then the running, then the rest
  const order = (n: NodeInfo) => ["stopped", "needs_you", "paused", "running", "idle"]
    .indexOf(primary(statuses.get(n.node_id)!));
  const sorted = [...list].sort((a, b) => order(a) - order(b) || a.node_id.localeCompare(b.node_id));
  return (
    <div style={{ fontFamily: MONO, fontSize: 12 }}>
      <div style={{ color: C.mut, fontSize: 11, marginBottom: 6 }}>agents · {list.length}</div>
      {sorted.map((n) => {
        const p = primary(statuses.get(n.node_id)!);
        const row = rows.get(n.node_id);
        return (
          <div key={n.node_id} onClick={() => onPick(n.node_id)}
            className="flex items-center gap-2"
            style={{ padding: "5px 8px", borderRadius: 5, cursor: "pointer",
                     background: sel === n.node_id ? "rgba(127,168,204,0.10)" : "transparent" }}>
            <Circle size={7} fill={STATUS_COLOR[p]} color={STATUS_COLOR[p]} />
            <span style={{ color: C.text, flex: 1, minWidth: 0, overflow: "hidden",
                           textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={n.node_id}>{n.node_id}</span>
            {row?.active_run
              ? <span style={{ color: C.green, fontSize: 10 }}>{row.current_tool ?? "run"}</span>
              : <span style={{ color: C.dim, fontSize: 10 }}>{row?.runs ?? 0}</span>}
          </div>
        );
      })}
    </div>
  );
}

function feedLine(e: ActivityEvent): { text: string; color: string } {
  const p = e.payload;
  const tool = typeof p.tool === "string" ? p.tool : "";
  switch (e.kind) {
    case "tool_call":
      return e.verdict === "deny" ? { text: `→ ${tool} denied`, color: C.red } : { text: `→ ${tool}`, color: C.text };
    case "tool_result": {
      const src = ((p.root as { sources?: string[] } | undefined)?.sources ?? []).join(", ");
      return src ? { text: `← ${tool} · untrusted (${src})`, color: C.amber } : { text: `← ${tool}`, color: C.text };
    }
    case "claim": return { text: `answered: ${String(p.text ?? "").slice(0, 60)}`, color: C.mut };
    case "fault_injected": return { text: `⚡ ${tool} broken on purpose`, color: C.steel };
    case "denial": return { text: `denied ${tool}`, color: C.red };
    case "fact": return { text: `fact ${String(p.fact_type ?? "")}`, color: C.amber };
    default: {
      const t = describeAck(e.kind, p);
      return { text: t, color: e.kind.endsWith("refused") ? C.red : C.steel };
    }
  }
}

function ActivityFeed() {
  const feed = useQuery({ queryKey: ["activity"], queryFn: api.activity, refetchInterval: 2000 });
  const [find, setFind] = useState("");
  const events = (feed.data?.events ?? []).filter((e) => {
    if (!find) return true;
    const t = `${e.node_id} ${feedLine(e).text}`.toLowerCase();
    return t.includes(find.toLowerCase());
  });
  const work = new Set(["tool_call", "tool_result", "claim", "fault_injected", "denial"]);
  return (
    <div style={{ fontFamily: MONO, fontSize: 11 }}>
      <div className="flex items-center gap-2" style={{ marginBottom: 6 }}>
        <span style={{ color: C.mut }}>activity</span>
        <span style={{ flex: 1 }} />
        <input value={find} onChange={(e) => setFind(e.target.value)} placeholder="find" aria-label="filter activity"
          style={{ width: 120, background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4,
                   color: C.text, fontFamily: MONO, fontSize: 11, padding: "3px 7px", outline: "none" }} />
      </div>
      <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
        {events.length === 0 && (
          <div className="p-3" style={{ color: C.dim }}>
            {feed.isLoading ? "loading…" : "nothing yet — agent steps and operator acks land here as they happen."}
          </div>
        )}
        {events.map((e, i) => {
          const { text, color } = feedLine(e);
          // agent work opens its run; an ack opens the agent it came from
          const go = () => work.has(e.kind)
            ? navigate(`govern/run/${encodeURIComponent(e.run_id)}`)
            : navigate(`control/${encodeURIComponent(e.node_id)}`);
          return (
            <div key={i} onClick={go} className="px-3 py-1.5"
              style={{ borderTop: i === 0 ? "none" : `1px solid ${C.line}`, cursor: "pointer" }}>
              <div className="flex gap-2">
                <span style={{ color: C.dim }}>{e.ts ? new Date(e.ts).toTimeString().slice(0, 8) : ""}</span>
                <span style={{ color: C.steel, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis",
                               whiteSpace: "nowrap" }}>{e.node_id}</span>
              </div>
              <div style={{ color, overflowWrap: "anywhere" }}>{text}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
