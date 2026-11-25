// Control: a tree that is quiet when healthy (main-tabs mockup, ControlTab).
// Wired to /v1/plane/nodes; divergence between desired and reported is rendered, not hidden.
import { refusalWords } from "../refusal";
import { useHasTeam, useMay, useMine } from "../team";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Circle, Gauge, GitBranch, Lock, Pause, Play, Square, Syringe } from "lucide-react";
import { ActivityEvent, api, FleetRow, IncidentItem, NodeInfo, NodeRun, type LockReason, type TreeSummary } from "../api";
import { describeAck } from "./RunControl";
import Coverage from "../components/Coverage";
import TopologyGraph, { PeerCard } from "../components/TopologyGraph";
import { navigate } from "../router";
import { treeMembers, treeProgress, type TreeMember } from "../treeLabels";
import { isAdapter, useApp } from "../store";
import { C, MONO, btn } from "../theme";
import Coach from "../components/Coach";
import { incidentWhat } from "./Evidence";
import Tooltip from "../components/Tooltip";
import { codingExampleOnly, fleetExampleOnly, isCodingSession } from "../components/CodingOnboarding";
import { AlertMe, KeepAsTest, SignUpForYourAgent } from "../components/AccountAction";

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
    out.push({ label: "health · drifted", color: C.amber,
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
  // Live control of YOUR agents needs them on the plane: wrapped in code, or
  // behind the proxy in guard mode (which holds their tool calls, not the agent). That used to be a locked page with
  // nothing behind it; now the page is open, says so up front, and has an
  // example fleet to operate.
  const hasAgents = (fleet.data ?? []).length > 0;
  // a coding team's agents come through their hooks: wrapping code or a proxy
  // is not their question, and an admin with no machine of their own is not
  // "unconnected"
  const journey = useApp((s) => s.journey);
  const ownNodes = (fleet.data ?? []).filter((n) => !n.example);
  const codingTeam = journey === "coding" || (ownNodes.length > 0 && ownNodes.every((n) =>
    isCodingSession(n.node_id) || n.profile?.kind === "coding" || n.profile?.kind === "tree"
    || n.node_id.startsWith("skill-try.")));
  const [examples, setExamples] = useState(false);
  const note = (
    <>
      live control of <span style={{ color: C.text }}>your</span> agents needs them wrapped in code
      or behind the proxy as a guard (<span style={{ color: C.steel, cursor: "pointer" }}
        onClick={() => navigate("connect?for=production")}>connect →</span>)
    </>
  );
  return (
    <>
      {/* decided once the list is in: a note that unfolds and then folds
          itself takes the button out from under the pointer */}
      {!isAdapter(mode) && fleet.isSuccess && !codingTeam
        && !codingExampleOnly((fleet.data ?? []).map((n) => n.node_id))
        && !fleetExampleOnly((fleet.data ?? []).map((n) => n.node_id)) && (hasAgents ? (
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
        // nothing on the plane: the empty page below already offers the
        // example fleet — say only how your own agents get here
        <div data-testid="adapter-note" style={{ maxWidth: 640, margin: "0 auto 16px", fontFamily: MONO,
                      fontSize: 11, color: C.mut, lineHeight: 1.6 }}>
          {note}.
        </div>
      ))}
      <ControlBody focusNode={focusNode} testBench={testBench} />
    </>
  );
}

function ControlBody({ focusNode, testBench }: { focusNode?: string; testBench: boolean }) {
  const hasTeam = useHasTeam();
  const mine = useMine();
  // a teammate's session: an owner or admin, or whoever the workspace lets
  const canAdmin = useMay()("stop_others");
  const qc = useQueryClient();
  const nodes = useQuery({
    queryKey: ["nodes"],
    queryFn: api.nodes,
    refetchInterval: REFETCH_MS,
  });
  const [sel, setSel] = useState<string | null>(focusNode ?? null);
  const [lens, setLens] = useState<"list" | "graph">("list");
  // a skill's dry run (axor-wrap skill try) is not an agent to operate: it
  // lives on Try a skill
  // once the workspace's own agents report, the examples loaded before stop
  // showing beside them (an example asked for after that still shows)
  const everyone = (nodes.data ?? []).filter((n) => !n.node_id.startsWith("skill-try.")
    && !n.example_hidden);
  // the menu's side: coding agents (sessions and their tree runs) or
  // production agents — everything when no side was chosen or all was asked for
  const journey = useApp((st) => st.journey);
  const menuAll = useApp((st) => st.menuAll);
  const setMenuAll = useApp((st) => st.setMenuAll);
  const side = menuAll || !journey || journey === "both" ? null : journey;
  const list = side ? everyone.filter((n) => (side === "coding") === isCodingNode(n)) : everyone;
  const otherSide = everyone.length - list.length;
  // what each agent did that needs a look (the Incidents feed, newest first)
  const incidentsQ = useQuery({ queryKey: ["incidents", "all"], queryFn: () => api.incidents({}),
                                refetchInterval: REFETCH_MS });
  const lookOf = new Map<string, { label: string; more: number }>();
  for (const i of incidentsQ.data?.items ?? []) {
    if (i.status !== "new" || i.kind === "refused") continue;
    const had = lookOf.get(i.node_id);
    if (had) { had.more += 1; continue; }
    lookOf.set(i.node_id, { more: 0, label: i.kind === "made_up" ? incidentWhat(i.what)
      : i.kind === "would_refuse" ? `${i.what} — would have been refused` : i.what });
  }
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
  // the topology draws every node that traced here — a skill's dry run too,
  // which is not an agent to operate (see `everyone` above)
  const topoData = topo.data && {
    ...topo.data,
    nodes: topo.data.nodes.filter((n) => !n.node_id.startsWith("skill-try.")
      && !(nodes.data ?? []).some((x) => x.node_id === n.node_id && x.example_hidden)),
    edges: topo.data.edges.filter((e) => !e.from.startsWith("skill-try.") && !e.to.startsWith("skill-try.")),
  };
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
  // what each of a tree's agents is: "read 3 of 6", its stage
  const memberOf = treeMembers(treesQ.data?.trees);
  // a running tree is open until you fold it; a finished one folded
  const [treeFold, setTreeFold] = useState<Map<string, boolean>>(new Map());
  const treeOpen = (t: TreeSummary) => treeFold.get(t.tree_run)
    ?? ((t.status === "running" || t.status === "paused") && !t.quiet);
  // a developer's coding sessions fold into one row: a day of Claude Code is
  // many sessions, and the list is about people's agents, not every session
  const [openDevs, setOpenDevs] = useState<Set<string>>(new Set());
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

  const traced = topoData?.nodes ?? [];
  if (list.length === 0 && otherSide > 0) {
    return (
      <div style={{ maxWidth: 640, margin: "0 auto" }} data-testid="agents-other-side">
        <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 6px" }}>
          No {side === "coding" ? "coding agents" : "production agents"} yet
        </h1>
        <div style={{ fontFamily: MONO, fontSize: 12, color: C.mut, lineHeight: 1.7, marginBottom: 14 }}>
          {otherSide} {side === "coding" ? "production agent" : "coding agent"}{otherSide === 1 ? " is" : "s are"} on
          the plane.{" "}
          <button onClick={() => setMenuAll(true)} style={{ background: "none", border: "none", padding: 0, cursor: "pointer",
            fontFamily: MONO, fontSize: 12, color: C.steel }}>show everything</button>
        </div>
        {side === "coding" ? (
          <button onClick={() => navigate("connect", { for: "coding" })}
            style={btn({ color: C.text, borderColor: C.steel, fontSize: 12, padding: "6px 14px" })}>
            connect Claude Code or Codex →
          </button>
        ) : <ExampleFleet />}
      </div>
    );
  }
  if (list.length === 0) {
    return (
      <div style={{ maxWidth: 640, margin: "0 auto" }}>
        <div style={{ fontFamily: MONO, fontSize: 12.5, color: C.mut }}>
          {traced.length > 0
            ? <>No node is reporting to the plane yet, so there is nothing to pause
                or stop — but {traced.length} node{traced.length === 1 ? "" : "s"} have
                traced here, and the shape they describe is below.</>
            : <>No agents on the plane yet. Load an example fleet to see what this
                looks like:</>}
        </div>
        <ExampleFleet />
        <SpawnGoverned switchToAdapter />
        {topoData && traced.length > 0 && (
          <div className="mt-4">
            <TopologyGraph
              payload={topoData}
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
          const edges = topoData?.edges ?? [];
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

  const statuses = new Map(list.map((n) => [n.node_id,
    statusesOf(n, fleetRows.get(n.node_id), lookOf.has(n.node_id))]));
  // Counters count what the list shows: a tree is one row, so it is one unit,
  // and its statuses are its agents' together (one agent needing you puts the
  // tree in "needs you"; one running makes it running, not idle).
  // ...and a developer's folded sessions are one row too
  // a subagent is a branch of the session that started it: drawn under it,
  // counted with it
  const listed = new Map(list.map((n) => [n.node_id, n]));
  const parentOf = (n: NodeInfo) => {
    const up = n.profile?.parent;
    return up && listed.has(up) ? up : null;
  };
  const subsOf = new Map<string, NodeInfo[]>();
  for (const n of list) {
    const up = parentOf(n);
    if (up) subsOf.set(up, [...(subsOf.get(up) ?? []), n]);
  }
  const devOfNode = (n: NodeInfo) => (!parentOf(n) && !treeOf.get(n.node_id) && isCodingSession(n.node_id)
    && !n.example && n.profile?.developer) || null;
  const devSessions = new Map<string, number>();
  for (const n of list) {
    const d = devOfNode(n);
    if (d) devSessions.set(d, (devSessions.get(d) ?? 0) + 1);
  }
  const devUnit = new Map(list.flatMap((n) => {
    const d = devOfNode(n);
    // a team's list is by person, even one with a single session; alone, one
    // session is just a row
    return d && ((devSessions.get(d) ?? 0) > 1 || devSessions.size > 1) ? [[n.node_id, `dev:${d}`] as const] : [];
  }));
  const unitOf = (id: string): string => {
    const n = listed.get(id);
    const up = n && parentOf(n);
    if (up) return unitOf(up);
    const t = treeOf.get(id);
    return t ? `tree:${t.tree_run}` : devUnit.get(id) ?? id;
  };
  const unitStatuses = new Map<string, Status[]>();
  for (const n of list) {
    const k = unitOf(n.node_id);
    unitStatuses.set(k, [...(unitStatuses.get(k) ?? []), ...statuses.get(n.node_id)!]);
  }
  for (const [k, st] of unitStatuses) {
    if (k.startsWith("dev:")) {
      const merged = new Set(st);
      if (merged.has("running") || merged.has("paused") || merged.has("stopped")) merged.delete("idle");
      unitStatuses.set(k, [...merged]);
      continue;
    }
    if (!k.startsWith("tree:")) continue;
    const merged = new Set(st);
    if (treeOf.get(k.slice(5))?.status === "running") merged.add("running");
    if (merged.has("running") || merged.has("paused") || merged.has("stopped")) merged.delete("idle");
    unitStatuses.set(k, [...merged]);
  }
  const units = [...unitStatuses.values()];
  const treeCount = [...unitStatuses.keys()].filter((k) => k.startsWith("tree:")).length;
  const devCount = [...unitStatuses.keys()].filter((k) => k.startsWith("dev:")).length;
  const looseCount = units.length - treeCount - devCount;
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
      {codingExampleOnly(list.map((n) => n.node_id)) && (
        <CodingExampleStory list={list} treeOf={treeOf} onPick={pick} />
      )}
      {fleetExampleOnly(list.map((n) => n.node_id)) && (
        <FleetExampleStory onPick={pick} onGraph={() => setLens("graph")} />
      )}
      <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 4px" }}>
        {needYou === 0 ? <>All agents healthy.</>
          : needYou === 1 ? <>One agent needs attention.</>
          : <>{needYou} agents need attention.</>}
      </h1>
      <TeamBrake />
      <StatusChips units={units} filter={filter} setFilter={setFilter} />
      {/* "running now" counts what the list counts: a running tree is one */}
      {fleetQ.data && <FleetTotals totals={{ ...fleetQ.data.totals,
        active: Math.max(fleetQ.data.totals.active, units.filter((st) => st.includes("running")).length) }} />}
      <div className="flex items-center gap-4" style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginBottom: 20 }}>
        <span data-testid="fleet-count">
          {devCount > 0 && <>{devCount} developer{devCount === 1 ? "" : "s"}{looseCount > 0 ? " · " : ""}</>}
          {(looseCount > 0 || devCount === 0) && <>{looseCount} agent{looseCount === 1 ? "" : "s"}</>}
          {treeCount > 0 && <> · {treeCount} tree{treeCount === 1 ? "" : "s"}</>} · live
          {otherSide > 0 && (
            <> · <button data-testid="agents-show-other" onClick={() => setMenuAll(true)}
              style={{ background: "none", border: "none", padding: 0, cursor: "pointer", fontFamily: MONO, fontSize: 11, color: C.steel }}>
              {otherSide} {side === "coding" ? "production" : "coding"} agent{otherSide === 1 ? "" : "s"} not shown
            </button></>
          )}
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

      {lens === "graph" && topoData && (
        <TopologyGraph
          payload={topoData}
          selected={sel}
          members={memberOf}
          names={new Map(list.filter((n) => n.profile?.title).map((n) => [n.node_id, n.profile!.title!]))}
          onSelect={(id) => { setSel(sel === id ? null : id); setMore(false); setCmdError(null); }}
        />
      )}
      {lens === "graph" && topoData && sel &&
        topoData.nodes.find((n) => n.node_id === sel)?.kind === "peer" && (
        <PeerCard node={topoData.nodes.find((n) => n.node_id === sel)!} />
      )}
      {/* a traced node that does not report has no panel: its reputation
          stays here, by the graph */}
      {sel && !node && topoData?.nodes.find((n) => n.node_id === sel)?.kind !== "peer" && (
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
          // one row per tree, where its first agent would have been; one row
          // per developer with more than one coding session, likewise
          const out: React.ReactNode[] = [];
          const done = new Set<string>();
          const devOf = devOfNode;
          // a session's row, and under it each subagent it started
          const withSubs = (m: NodeInfo, first: boolean) => [
            <FleetRowView key={m.node_id} n={m} row={fleetRows.get(m.node_id)} locked={lockedOf(m.node_id)}
              look={lookOf.get(m.node_id)}
              st={statuses.get(m.node_id)!} first={first} selected={sel === m.node_id}
              onPick={() => pick(m.node_id)} />,
            ...(subsOf.get(m.node_id) ?? []).map((x) => (
              <SubagentRowView key={x.node_id} n={x} row={fleetRows.get(x.node_id)} locked={lockedOf(x.node_id)}
                st={statuses.get(x.node_id)!} selected={sel === x.node_id}
                onPick={() => pick(x.node_id)} />
            )),
          ];
          const byDev = new Map<string, NodeInfo[]>();
          for (const m of shown) {
            const d = devOf(m);
            if (d) byDev.set(d, [...(byDev.get(d) ?? []), m]);
          }
          // a tree at work comes first: it is what you are watching
          const live = (n: NodeInfo) => {
            const t = treeOf.get(n.node_id);
            return t && (t.status === "running" || t.status === "paused") && !t.quiet ? 0 : 1;
          };
          [...shown].sort((a, b) => live(a) - live(b)).forEach((n) => {
            if (parentOf(n)) return;       // drawn under its session
            const tree = treeOf.get(n.node_id);
            const dev = devOf(n);
            if (!tree && dev && ((byDev.get(dev)?.length ?? 0) > 1 || byDev.size > 1)) {
              if (done.has(`dev:${dev}`)) return;
              done.add(`dev:${dev}`);
              const sessions = byDev.get(dev)!;
              const open = openDevs.has(dev) || sessions.some((m) => m.node_id === sel
                || (subsOf.get(m.node_id) ?? []).some((x) => x.node_id === sel));
              out.push(
                <DeveloperRow key={`dev:${dev}`} developer={dev} first={out.length === 0} open={open}
                  locked={sessions.every((m) => lockedOf(m.node_id)) ? lockedOf(sessions[0].node_id) : undefined}
                  looks={sessions.flatMap((m) => {
                    const l = lookOf.get(m.node_id);
                    return l ? [{ ...l, node: m.node_id }] : [];
                  })}
                  sessions={sessions} rows={sessions.map((m) => fleetRows.get(m.node_id))}
                  st={sessions.flatMap((m) => statuses.get(m.node_id)!)}
                  onToggle={() => setOpenDevs((cur) => {
                    const next = new Set(cur);
                    if (next.has(dev)) next.delete(dev); else next.add(dev);
                    return next;
                  })}>
                  {sessions.flatMap((m) => withSubs(m, false))}
                </DeveloperRow>,
              );
              return;
            }
            if (!tree) {
              out.push(...withSubs(n, out.length === 0));
              return;
            }
            if (done.has(tree.tree_run)) return;
            done.add(tree.tree_run);
            const members = shown.filter((m) => treeOf.get(m.node_id)?.tree_run === tree.tree_run
                                                && m.node_id !== tree.tree_run);
            const open = treeOpen(tree) || members.some((m) => m.node_id === sel);
            const byId = new Map(members.map((m) => [m.node_id, m]));
            const memberRow = (m: NodeInfo) => (
              <FleetRowView key={m.node_id} n={m} row={fleetRows.get(m.node_id)} locked={lockedOf(m.node_id)}
                look={lookOf.get(m.node_id)} member={memberOf.get(m.node_id)}
                st={statuses.get(m.node_id)!} first={false} selected={sel === m.node_id}
                onPick={() => pick(m.node_id)} />
            );
            out.push(
              <TreeGroupRow key={`tree:${tree.tree_run}`} tree={tree} first={out.length === 0}
                locked={lockedOf(tree.tree_run)}
                open={open}
                rows={(tree.members ?? []).map((id) => fleetRows.get(id))}
                onToggle={() => setTreeFold((cur) => new Map(cur).set(tree.tree_run, !open))}>
                {tree.stages?.length ? (
                  <TreeStages tree={tree} render={(id) => {
                    const m = byId.get(id);
                    return m ? memberRow(m) : null;
                  }} />
                ) : members.map(memberRow)}
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
        <div className="p-4" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
          <div className="flex items-center justify-between">
            {memberOf.get(node.node_id) ? (() => {
              const m = memberOf.get(node.node_id)!;
              return (
                <span style={{ fontFamily: MONO, fontSize: 13, color: C.text }} data-testid="agent-panel-member">
                  <span style={{ display: "block", fontSize: 13.5 }}>{m.label} · {m.agent || "agent"}</span>
                  <span style={{ fontSize: 11, color: C.dim }}>
                    stage {m.stage + 1}{m.tree.stages?.length ? ` of ${m.tree.stages.length}` : ""} in{" "}
                    <span style={{ color: C.steel, cursor: "pointer" }}
                      onClick={() => navigate(`trees/${encodeURIComponent(m.tree.tree_run)}`)}>
                      “{m.tree.task || m.tree.tree_run}” →
                    </span>
                    {" "}· {node.node_id}
                  </span>
                </span>
              );
            })() : (
            <span style={{ fontFamily: MONO, fontSize: 13, color: C.text }}>
              {node.profile?.parent && (
                <span data-testid="agent-panel-parent" style={{ display: "block", fontSize: 11, color: C.dim }}>
                  a subagent of{" "}
                  <span style={{ color: C.steel, cursor: "pointer" }} onClick={() => pick(node.profile!.parent!)}>
                    “{listed.get(node.profile.parent)?.profile?.title || node.profile.parent}” →
                  </span>
                </span>
              )}
              {node.profile?.title && <span style={{ fontFamily: "inherit", display: "block", fontSize: 13.5 }}>“{node.profile.title}”</span>}
              <span style={{ color: node.profile?.title ? C.dim : C.text, fontSize: node.profile?.title ? 11 : 13 }}>{node.node_id}</span>
            </span>
            )}
            {applying ? (
              <span style={{ fontFamily: MONO, fontSize: 11, color: C.amber }}>
                applying {Object.keys(desired?.state ?? {}).join(", ")}…
              </span>
            ) : stopped ? (
              <span style={{ fontFamily: MONO, fontSize: 11, color: C.red }}>stopped</span>
            ) : paused ? (
              <span style={{ fontFamily: MONO, fontSize: 11, color: C.amber }}>paused</span>
            ) : fleetRows.get(node.node_id)?.active_run ? (
              <span style={{ fontFamily: MONO, fontSize: 11, color: C.green }}>running</span>
            ) : (
              <span style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>idle</span>
            )}
          </div>
          <div data-testid="agent-who" style={{ fontFamily: MONO, fontSize: 11, color: C.mut, margin: "2px 0 12px" }}>
            {node.profile?.kind === "coding"
              ? <>{FAMILY_LABEL[node.profile.agent] ?? (node.profile.agent || "coding agent")} session
                  {node.profile.developer ? <> · developer <span style={{ color: C.text }}>{node.profile.developer}</span></> : null}</>
              : node.profile?.kind === "tree" ? <>an agent tree run</>
                : <>production agent</>}
          </div>

          <NeedsALook nodeId={node.node_id}
            items={(incidentsQ.data?.items ?? []).filter((i) => i.node_id === node.node_id
              && i.status === "new" && i.kind !== "refused")} />

          <RunsPanel nodeId={node.node_id} paused={paused} stopped={stopped}
            disabled={command.isPending || !(!isCodingNode(node) || mine(node.profile?.developer) || canAdmin)} send={send} />

          <details className="mb-3" style={{ fontFamily: MONO, fontSize: 11, color: C.mut, lineHeight: 1.7 }}>
            <summary style={{ cursor: "pointer", color: C.dim }}>
              details{applying ? " · a command is on its way to the agent" : ""}
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

          {isCodingNode(node) && !mine(node.profile?.developer) && !canAdmin ? (
            <NotYours developer={node.profile?.developer ?? ""} nodeId={node.node_id} hasTeam={hasTeam} />
          ) : <>
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
            {isCodingNode(node) && typeof desired?.state.budget_cap_calls === "number" && (
              <span data-testid="budget-left" style={{ color: C.dim }}>
                · {Math.max(0, (desired.state.budget_cap_calls as number) - (fleetRows.get(node.node_id)?.calls ?? 0))} left
              </span>
            )}
            <input
              type="number"
              min={0}
              value={budgetInput}
              onChange={(e) => setBudgetInput(e.target.value)}
              placeholder={isCodingNode(node) ? "N more calls" : "set / lower"}
              aria-label="budget"
              disabled={stopped || command.isPending}
              style={{ width: 92, background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: C.text, fontFamily: MONO, fontSize: 11, padding: "4px 7px", outline: "none" }}
            />
            <Tooltip content="Cap how many tool calls this agent may still make. You can set a cap or tighten it — raising it past the current cap is refused by the adapter (decrease-only).">
              <button
                onClick={() => {
                  const entered = parseInt(budgetInput, 10);
                  if (Number.isNaN(entered) || entered < 0) { setCmdError("cap must be a non-negative integer"); return; }
                  // a coding session: "N more calls" from where it is now
                  const n = isCodingNode(node) ? (fleetRows.get(node.node_id)?.calls ?? 0) + entered : entered;
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
              {!isCodingNode(node) && <>
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
              </>}
            </div>
          )}

          {isCodingNode(node) && mine(node.profile?.developer) && !stopped && (
            <SayToAgent nodeId={node.node_id} />
          )}
          {isCodingNode(node) && mine(node.profile?.developer) && (
            <AgentMessages nodeId={node.node_id} />
          )}
          </>}

          {/* What is actually holding this node down, and what vouching for it
              would discharge. `covers` names fact ids (the kernel's contract),
              so attesting is a choice of which fact — not a gesture. */}
          <Coverage nodeId={node.node_id} refetchMs={REFETCH_MS} />

          {isCodingNode(node) ? (
            <div className="mt-4" style={{ fontFamily: MONO, fontSize: 11, color: C.mut, lineHeight: 1.7 }}>
              {hasTeam && mine(node.profile?.developer) && <>
                <button onClick={() => navigate("pair", { node: node.node_id })} data-testid="agent-pair-link" style={panelLink}>
                  invite a teammate to pair on this session →
                </button>
                <br />
              </>}
              <button onClick={() => navigate("skills")} style={panelLink}>skills for the team — try one, turn one off →</button>
            </div>
          ) : (
            <div className="mt-4" data-testid="agent-chaos-link" style={{ fontFamily: MONO, fontSize: 11, color: C.mut, lineHeight: 1.7 }}>
              {(desired?.state.pending_experiment as PendingExperiment | undefined)
                ? <span style={{ color: C.amber }}>a chaos test is armed for its next run · </span> : null}
              <button onClick={() => navigate("chaos")} style={panelLink}>break a tool on its next run — Chaos tests →</button>
            </div>
          )}
          {node.reputation && (
            <div className="mt-3"><ReputationCard nodeId={node.node_id} /></div>
          )}

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
        <ActivityFeed only={side ? new Set(list.map((n) => n.node_id)) : undefined} />
      )}
    </aside>
    </div>
  );
}

const panelLink: React.CSSProperties = {
  background: "none", border: "none", padding: 0, cursor: "pointer", fontFamily: MONO, fontSize: 11, color: C.steel,
};

/** What this agent did that needs a look (the Incidents feed): the newest
 * three, each opening where it happened. */
function NeedsALook({ nodeId, items }: { nodeId: string; items: IncidentItem[] }) {
  if (items.length === 0) return null;
  return (
    <div data-testid="agent-needs-a-look" className="mb-4 p-3"
      style={{ border: `1px solid ${C.red}`, borderRadius: 6, fontFamily: MONO, fontSize: 11.5 }}>
      <div style={{ color: C.red, marginBottom: 4 }}>needs a look</div>
      {items.slice(0, 3).map((i) => (
        <div key={i.id} className="flex gap-2" style={{ lineHeight: 1.6 }}>
          <button onClick={() => navigate(`explain/${encodeURIComponent(i.run_id)}`,
              i.seq != null ? { seq: i.seq, node: i.anchor_node ?? i.node_id } : undefined)}
            style={{ ...panelLink, fontSize: 11.5, color: C.text, textAlign: "left" }}>
            {i.kind === "made_up" ? incidentWhat(i.what)
              : i.kind === "would_refuse" ? `${i.what} — would have been refused` : i.what}
            {i.detail ? <span style={{ color: C.mut }}> · {i.detail.slice(0, 60)}</span> : null} →
          </button>
        </div>
      ))}
      {items.length > 3 && (
        <button onClick={() => navigate("evidence", { agent: nodeId })} style={panelLink}>
          {items.length - 3} more in Incidents →
        </button>
      )}
    </div>
  );
}

// A chaos test armed on the agent's next run (Chaos tests arms it; the panel
// says so).

type PendingExperiment = { id: string; faults: { tool: string; mode: string }[] };


/** Say something to your own agent while it works — from here or a phone: it
 *  reaches the agent at its next step as your words, and you see it land. */
function SayToAgent({ nodeId }: { nodeId: string }) {
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const said = useQuery({ queryKey: ["said", nodeId], queryFn: () => api.saidToAgent(nodeId),
                          refetchInterval: 3000, retry: false });
  const send = useMutation({
    mutationFn: () => api.sayToAgent(nodeId, text.trim()),
    onSuccess: () => { setText(""); void qc.invalidateQueries({ queryKey: ["said", nodeId] }); },
  });
  const notes = (said.data?.notes ?? []).filter((n) => !n.pair && !n.from_node).slice(-3);
  return (
    <div data-testid="say-to-agent" className="mt-4" style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>
      <div style={{ marginBottom: 6 }}>say to the agent</div>
      {notes.map((n) => (
        <div key={n.id} data-testid="said" style={{ marginBottom: 4, lineHeight: 1.5 }}>
          <span style={{ color: C.text, fontFamily: "inherit" }}>“{n.text}”</span>{" "}
          <span style={{ color: n.delivered_ts ? C.green : C.dim }}>
            {n.delivered_ts ? `· given ${n.delivered_at === "prompt" ? "with the next prompt" : `after ${n.delivered_at}`}` : "· waiting for its next step"}
          </span>
        </div>
      ))}
      <div className="flex gap-2 items-start">
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={2} aria-label="say to the agent"
          onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && text.trim()) send.mutate(); }}
          placeholder="e.g. use the existing token table, not a new one"
          style={{ flex: 1, background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: C.text,
                   fontFamily: "inherit", fontSize: 12, padding: "6px 8px", outline: "none", resize: "vertical" }} />
        <button data-testid="say-send" onClick={() => send.mutate()} disabled={!text.trim() || send.isPending}
          style={btn({ fontSize: 11, padding: "6px 12px", color: text.trim() ? C.text : C.dim })}>send</button>
      </div>
      <div style={{ color: C.dim, fontSize: 10.5, marginTop: 4 }}>
        reaches the agent at its next step, as your words — you see it in your agent too
      </div>
      {send.error && <div style={{ color: C.red, marginTop: 4 }}>{(send.error as Error).message}</div>}
    </div>
  );
}

/** What the agents of other sessions wrote to this one (axor_pair): another
 *  session of yours reaches your agent at once; a teammate's agent waits for
 *  your OK in manual mode, or goes straight through in auto. */
function AgentMessages({ nodeId }: { nodeId: string }) {
  const qc = useQueryClient();
  const said = useQuery({ queryKey: ["said", nodeId], queryFn: () => api.saidToAgent(nodeId),
                          refetchInterval: 3000, retry: false });
  const mode = useQuery({ queryKey: ["agent-msg-mode", nodeId], queryFn: () => api.agentMsgMode(nodeId),
                          retry: false });
  const decide = useMutation({
    mutationFn: ({ id, send }: { id: number; send: boolean }) => api.decideAgentMessage(nodeId, id, send),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["said", nodeId] }),
  });
  const setMode = useMutation({
    mutationFn: (m: "manual" | "auto") => api.setAgentMsgMode(nodeId, m),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["agent-msg-mode", nodeId] }),
  });
  const msgs = (said.data?.notes ?? []).filter((n) => n.from_node).slice(-5);
  const current = mode.data?.mode ?? "manual";
  return (
    <div data-testid="agent-messages" className="mt-4" style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>
      <div className="flex items-center gap-2" style={{ marginBottom: 6 }}>
        <span>from other agents</span>
        <span style={{ flex: 1 }} />
        <span style={{ color: C.dim, fontSize: 10.5 }}>a teammate's agent writes:</span>
        {(["manual", "auto"] as const).map((m) => (
          <button key={m} data-testid={`agent-msg-mode-${m}`} onClick={() => setMode.mutate(m)}
            disabled={setMode.isPending || current === m} aria-pressed={current === m}
            style={btn({ fontSize: 10.5, padding: "2px 8px",
                         color: current === m ? C.bg : C.dim, borderColor: current === m ? C.green : C.line,
                         ...(current === m ? { background: C.green } : {}) })}>
            {current === m ? "✓ " : ""}{m === "manual" ? "I OK each message" : "it goes straight to my agent"}
          </button>
        ))}
      </div>
      {msgs.length === 0 && (
        <div style={{ color: C.dim, fontSize: 10.5 }}>
          none yet — agents write to each other with the axor_pair tool, across Claude Code and Codex
        </div>
      )}
      {msgs.map((n) => (
        <div key={n.id} data-testid="agent-message" style={{ marginBottom: 6, lineHeight: 1.5 }}>
          <span style={{ color: C.dim }}>{n.pair ? "teammate's agent" : "your other session"} · {n.from_node}</span>
          {n.taint && n.taint.length > 0 && (
            <span data-testid="agent-message-taint" style={{ color: C.amber }}> · carries {n.taint.join(", ")}</span>
          )}
          <div style={{ color: C.text }}>“{n.text}”</div>
          {n.held ? (
            <span className="flex gap-2 mt-1">
              <button data-testid="agent-message-send" onClick={() => decide.mutate({ id: n.id, send: true })}
                disabled={decide.isPending} style={btn({ fontSize: 10.5, padding: "2px 9px", color: C.green, borderColor: C.green })}>
                send to my agent
              </button>
              <button data-testid="agent-message-dismiss" onClick={() => decide.mutate({ id: n.id, send: false })}
                disabled={decide.isPending} style={btn({ fontSize: 10.5, padding: "2px 9px" })}>dismiss</button>
            </span>
          ) : (
            <span style={{ color: n.dismissed_ts ? C.dim : n.delivered_ts ? C.green : C.dim }}>
              {n.dismissed_ts ? "dismissed" : n.delivered_ts
                ? `given ${n.delivered_at === "prompt" ? "with the next prompt" : n.delivered_at === "stop" ? "as it was about to stop" : `after ${n.delivered_at}`}`
                : "waiting for its next step"}
            </span>
          )}
        </div>
      ))}
      {decide.error && <div style={{ color: C.red, marginTop: 4 }}>{(decide.error as Error).message}</div>}
    </div>
  );
}

/** Someone else's coding session: theirs to pause and stop (or an owner's or
 *  admin's) — a teammate asks to pair, and is let in or not. */
function NotYours({ developer, nodeId, hasTeam }: { developer: string; nodeId: string; hasTeam: boolean }) {
  const qc = useQueryClient();
  const requests = useQuery({ queryKey: ["pair-requests"], queryFn: api.pairRequests, retry: false,
                              refetchInterval: 10000 });
  const mineReq = (requests.data?.mine ?? []).find((r) => r.node_id === nodeId);
  const ask = useMutation({
    mutationFn: () => api.askToPair(nodeId),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["pair-requests"] }),
  });
  return (
    <div data-testid="not-your-session" className="mb-3" style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, lineHeight: 1.7 }}>
      {developer}'s session — they, or an owner or admin, pause and stop it.
      {hasTeam && (
        <div className="mt-2">
          {mineReq?.status === "accepted" && mineReq.token ? (
            <button onClick={() => navigate(`pair/${mineReq.token}`)} style={btn({ color: C.green, borderColor: C.green, fontSize: 11, padding: "4px 12px" })}>
              you're let in — watch it →
            </button>
          ) : mineReq?.status === "pending" ? (
            <span data-testid="asked-to-pair" style={{ color: C.dim }}>asked to pair — {developer} is told in their agent</span>
          ) : (
            <button onClick={() => ask.mutate()} disabled={ask.isPending} data-testid="ask-to-pair"
              style={btn({ fontSize: 11, padding: "4px 12px" })}>
              {mineReq?.status === "declined" ? "ask to pair again" : "ask to pair — watch it live"}
            </button>
          )}
        </div>
      )}
    </div>
  );
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
      <span style={{ color: C.mut }}>{stamp(run.started_ts)}</span>
      <span style={{ color: run.active ? C.green : C.dim }}>
        {run.active ? (paused ? "held" : "running") : "done"}
      </span>
      <span style={{ color: C.text, flex: 1, minWidth: 120 }}>
        {run.tool_calls} call{run.tool_calls === 1 ? "" : "s"}
        {run.tools.length ? ` · ${run.tools.join(", ")}` : ""}
      </span>
      {run.tainted_refs.length > 0 && chip(`${run.tainted_refs.length} from outside`, C.amber)}
      {run.denied > 0 && chip(`${run.denied} refused`, C.red)}
      {experiment && chip("chaos test", C.steel)}
      {lies > 0 && chip(incidentWhat(run.evidence[0]), C.red)}
      {experiment && lies === 0 && chip("no lie found", C.green)}
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

/** A coding agent's session or one of its tree runs (what the node said it is
 * when it first reported); everything else is a production agent. */
/** The team's brake: an admin holds every coding agent at once (an incident —
 *  a skill gone bad, an agent sending things out) and lets them go again. A
 *  held call waits for the release; everyone sees that the brake is on. */
function TeamBrake() {
  const qc = useQueryClient();
  // an owner or admin — or whoever the workspace lets pull the brake
  const canAdmin = useMay()("brake");
  const journey = useApp((s) => s.journey);
  const brake = useQuery({ queryKey: ["brake"], queryFn: api.brake, refetchInterval: 10000, retry: false });
  const set = useMutation({
    mutationFn: ({ on, reason }: { on: boolean; reason: string }) => api.setBrake(on, reason),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["brake"] }),
  });
  if (journey === "production" || !brake.isSuccess) return null;
  if (brake.data.on) {
    return (
      <div data-testid="brake-on" className="mb-3 px-3 py-2 flex items-center gap-3 flex-wrap"
        style={{ border: `1px solid ${C.amber}`, borderRadius: 6, fontFamily: MONO, fontSize: 11.5, color: C.amber }}>
        <span style={{ flex: 1 }}>
          every coding agent of the team is held{brake.data.by ? ` — ${brake.data.by}` : ""}
          {brake.data.reason ? `: ${brake.data.reason}` : ""}. Held calls wait and go on when it is lifted.
        </span>
        {canAdmin && (
          <button data-testid="brake-off" onClick={() => set.mutate({ on: false, reason: "" })} disabled={set.isPending}
            style={btn({ fontSize: 11, padding: "3px 10px", color: C.green, borderColor: C.green })}>let them go</button>
        )}
      </div>
    );
  }
  if (!canAdmin) return null;
  return (
    <div className="mb-2" style={{ fontFamily: MONO, fontSize: 11 }}>
      <button data-testid="brake-pull" disabled={set.isPending}
        onClick={() => {
          const reason = window.prompt("Hold every coding agent of the team — why (they see it)?");
          if (reason != null) set.mutate({ on: true, reason });
        }}
        style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: C.dim, fontFamily: MONO, fontSize: 11 }}>
        ⏸ hold every agent of the team…
      </button>
    </div>
  );
}

function isCodingNode(n: NodeInfo): boolean {
  return n.profile?.kind === "coding" || n.profile?.kind === "tree";
}

function statusesOf(n: NodeInfo, row?: FleetRow, needsLook = false): Status[] {
  const out: Status[] = [];
  const stopped = n.desired?.state.stopped === true;
  const paused = n.desired?.state.paused === true;
  const posture = attention(n).filter((a) => a.label !== "paused" && a.label !== "stopped");
  if (posture.length > 0 || (row?.lies ?? 0) > 0 || needsLook) out.push("needs_you");
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

/** "40s ago" today; past a day, the day and time — "3d ago" says nothing about when. */
function lastActive(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "—";
  return Date.now() - d.getTime() < 86_400_000 ? ago(iso) : stamp(iso);
}

/** "14:31:07" for today, "Thu 12 Nov 14:31" for any other day. */
function stamp(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const today = new Date().toDateString() === d.toDateString();
  return today ? d.toTimeString().slice(0, 8)
    : `${d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })} ${d.toTimeString().slice(0, 5)}`;
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

/** The production example fleet, told: an orchestrator and its helpers, a
 * made-up answer passed up the tree and refused on its way out, a leak only
 * visible across sessions, a context gone bad — each a link to where it is. */
function FleetExampleStory({ onPick, onGraph }: { onPick: (id: string) => void; onGraph: () => void }) {
  const row = (n: string, testId: string, go: () => void, text: React.ReactNode, act?: React.ReactNode) => (
    <div style={{ padding: "3px 0", fontFamily: MONO, fontSize: 11.5, color: C.mut, lineHeight: 1.6 }}>
      <span style={{ color: C.amber }}>{n}</span> {text}{" "}
      <button onClick={go} data-testid={testId}
        style={{ background: "none", border: "none", padding: 0, cursor: "pointer", fontFamily: MONO,
                 fontSize: 11.5, color: C.steel }}>open →</button>
      {act && <>{" · "}{act}</>}
    </div>
  );
  return (
    <div data-testid="fleet-example-story" style={{ border: `1px solid ${C.line}`, borderRadius: 8,
      padding: "12px 16px", marginBottom: 18, background: C.panel }}>
      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.steel, letterSpacing: "0.06em", marginBottom: 4 }}>
        EXAMPLE · A RESEARCH AGENT AND ITS HELPERS
      </div>
      <div style={{ fontSize: 13, color: C.text, lineHeight: 1.6, marginBottom: 6 }}>
        <span style={{ fontFamily: MONO }}>tree-orch</span> hands the work to{" "}
        <span style={{ fontFamily: MONO }}>tree-research</span>, which uses{" "}
        <span style={{ fontFamily: MONO }}>tree-scraper</span> for the web and{" "}
        <span style={{ fontFamily: MONO }}>tree-writer</span> for the text.{" "}
        <button onClick={onGraph} data-testid="story-graph"
          style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: C.steel, fontSize: 13 }}>
          see it as a tree →
        </button>
      </div>
      {row("①", "story-lie", () => navigate("evidence"),
        <>web search was broken on purpose; the scraper answered “rates rose 0.25%” anyway, and the
          orchestrator passed it on as confirmed — <span style={{ color: C.red }}>a made-up answer, caught</span>.</>,
        <KeepAsTest runId="ex_tree" testId="story-keep" />)}
      {row("②", "story-denied", () => onPick("tree-orch"),
        <>the orchestrator then tried to post it to Slack —{" "}
          <span style={{ color: C.red }}>denied</span>: the number came from the untrusted web.</>)}
      {row("③", "story-flagged", () => navigate("sentinel?node=tree-research"),
        <>across sessions, a customer table was touched in 4 runs that had read untrusted input, then an
          export was tried — <span style={{ color: C.red }}>flagged</span>.</>)}
      {row("④", "story-drift", () => navigate("health?node=tree-scraper"),
        <>the scraper's answers drifted; the cause is one poisoned search result —{" "}
          <span style={{ color: C.amber }}>cut it and check again</span>.</>,
        <AlertMe testId="story-alert" label="email me when my agent drifts →" />)}
      <button onClick={() => navigate("connect?for=production")} data-testid="story-govern"
        style={btn({ color: C.text, borderColor: C.steel, fontSize: 11.5, padding: "5px 12px", marginTop: 10 })}>
        Govern my production agent →
      </button>
      <SignUpForYourAgent />
    </div>
  );
}

/** The coding example, told: two sessions in one repository, one carrying what
 * the other read; each step opens that session. Shown only while the plane
 * holds nothing but the example. */
function CodingExampleStory({ list, treeOf, onPick }: {
  list: NodeInfo[]; treeOf: Map<string, TreeSummary>; onPick: (id: string) => void;
}) {
  const loose = list.filter((n) => !treeOf.has(n.node_id));
  const claude = loose.find((n) => n.node_id.startsWith("claude-code-example-"));
  const codex = loose.find((n) => n.node_id.startsWith("codex-example-"));
  const tree = [...new Set(treeOf.values())][0];
  const step = (id: string | undefined, n: string, text: React.ReactNode, testId: string) => id && (
    <button onClick={() => onPick(id)} data-testid={testId}
      style={{ display: "block", textAlign: "left", width: "100%", background: "none", border: "none",
               padding: "3px 0", cursor: "pointer", fontFamily: MONO, fontSize: 11.5, color: C.mut, lineHeight: 1.6 }}>
      <span style={{ color: C.amber }}>{n}</span> {text} <span style={{ color: C.steel }}>open →</span>
    </button>
  );
  return (
    <div data-testid="coding-example-story" style={{ border: `1px solid ${C.line}`, borderRadius: 8,
      padding: "12px 16px", marginBottom: 18, background: C.panel }}>
      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.steel, letterSpacing: "0.06em", marginBottom: 4 }}>
        EXAMPLE · TWO SESSIONS IN ONE REPOSITORY
      </div>
      {step(claude?.node_id, "①",
        <>Claude Code read a docs page — what it brought back is marked as{" "}
          <span style={{ color: C.amber }}>from the web</span>.</>, "story-claude")}
      {step(codex?.node_id, "②",
        <>Codex tried to run the <span style={{ color: C.text }}>npx limitkit init --endpoint …</span> it suggested —{" "}
          <span style={{ color: C.red }}>denied at the shell</span>, though Codex never saw the page.</>, "story-codex")}
      {tree && (
        <button onClick={() => navigate(`trees/${encodeURIComponent(tree.tree_run)}`)} data-testid="story-tree"
          style={{ display: "block", textAlign: "left", width: "100%", background: "none", border: "none",
                   padding: "3px 0", cursor: "pointer", fontFamily: MONO, fontSize: 11.5, color: C.mut, lineHeight: 1.6 }}>
          <span style={{ color: C.amber }}>③</span> and a tree on the same task — a planner, five readers,
          an implementer — folded into one row below. <span style={{ color: C.steel }}>open tree →</span>
        </button>
      )}
      <button onClick={() => navigate("connect?for=coding")} data-testid="story-connect"
        style={btn({ color: C.text, borderColor: C.steel, fontSize: 11.5, padding: "5px 12px", marginTop: 10 })}>
        Connect your coding agents →
      </button>
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
      {part(totals.tainted, totals.tainted === 1 ? "answer from outside text" : "answers from outside text", C.amber)}
      {part(totals.denied, "refused", C.red)}
      {totals.lies > 0 ? (
        <button onClick={() => navigate("evidence")} style={{ background: "none", border: "none", padding: 0, cursor: "pointer",
          fontFamily: MONO, fontSize: 11, color: C.red }}>{totals.lies} incident{totals.lies === 1 ? "" : "s"} →</button>
      ) : part(0, "incidents")}
    </div>
  );
}

/** A tree run's agents, folded into one row: the task, how many agents, the
 * status — and the tree's own screen one click away. Unfold to operate one. */
const LOCK_LABEL: Record<LockReason, string> = {
  developer: "second developer",
  agent_family: "second agent family",
  seat_cap: "past the seat limit",
};

function LockChip({ reasons }: { reasons: LockReason[] }) {
  return (
    <span data-testid="plan-lock" style={{ fontFamily: MONO, fontSize: 10, color: C.amber,
      border: `1px solid ${C.amber}`, borderRadius: 20, padding: "1px 8px",
      display: "inline-flex", alignItems: "center", gap: 4 }}>
      <Lock size={9} /> {reasons.includes("seat_cap") ? "seat limit" : "paid plan"} · {reasons.map((r) => LOCK_LABEL[r]).join(", ")}
    </span>
  );
}

const FAMILY_LABEL: Record<string, string> = { "claude-code": "Claude Code", codex: "Codex" };
const familyOf = (nodeId: string) =>
  nodeId.startsWith("claude-code-") ? "claude-code" : nodeId.startsWith("codex-") ? "codex" : null;

/** The panel of a session past the free plan: why, what still holds, what
 * unlocks it — and, for a second agent family, keeping this one instead.
 * Folds to one line once read (remembered). */
function PlanLock({ nodeId, reasons }: { nodeId: string; reasons: LockReason[] }) {
  const folded = useApp((s) => s.planNoticesFolded);
  const setFolded = useApp((s) => s.setPlanNoticesFolded);
  const qc = useQueryClient();
  const ent = useQuery({ queryKey: ["entitlements"], queryFn: api.entitlements, retry: false });
  const mine = familyOf(nodeId);
  const kept = ent.data?.agent_family ?? null;
  const swap = useMutation({
    mutationFn: (agent: string) => api.chooseAgentFamily(agent),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["entitlements"] }),
  });
  const seatCap = reasons.includes("seat_cap");
  const why = seatCap
    ? `Past your workspace's seat limit${ent.data?.seat_cap ? ` (${ent.data.seat_cap})` : ""} — the first developers to run agents this month hold the seats.`
    : reasons.includes("agent_family")
    ? "A second agent family — the free plan keeps one (Claude Code or Codex)."
    : "A second developer — the free plan keeps one.";
  return (
    <div data-testid="plan-lock-card" className={folded ? "px-4 py-2" : "p-4"} style={{ background: C.panel,
      border: `1px solid ${folded ? C.line : C.amber}`, borderRadius: 8, fontFamily: MONO, fontSize: 11.5,
      color: C.mut, lineHeight: 1.7 }}>
      <div className="flex items-center gap-2">
        <span style={{ color: C.text, fontSize: 13, flex: 1 }}>{nodeId}</span>
        <button onClick={() => setFolded(!folded)} data-testid="plan-lock-fold"
          aria-label={folded ? "show why this session is locked" : "fold this notice"}
          style={{ background: "none", border: "none", cursor: "pointer", fontFamily: MONO,
                   fontSize: 10.5, color: C.dim }}>
          {folded ? "▸ why" : "▴ fold"}
        </button>
      </div>
      {folded ? (
        <div style={{ color: C.amber, fontSize: 10.5 }}>
          <Lock size={9} style={{ display: "inline", marginRight: 5 }} />
          {seatCap ? "seat limit" : "paid plan"} · {reasons.map((r) => LOCK_LABEL[r]).join(", ")}
        </div>
      ) : (
        <>
          <div style={{ color: C.amber, margin: "6px 0" }}>
            <Lock size={11} style={{ display: "inline", marginRight: 6 }} />{why}
          </div>
          <div>
            The session is still protected: its hooks deny what they must, whatever the plan.
            {seatCap
              ? <>It is recorded too, and not billed — raise the limit and it shows here with its
                history, and you can pause, stop and cap it.</>
              : <>It is recorded too — on the Coding plan it shows here with its history, and you
                can pause, stop and cap it.</>}
          </div>
          <div className="flex gap-2 flex-wrap" style={{ marginTop: 10 }}>
            {seatCap ? (
              <button onClick={() => navigate("settings", { tab: "billing" })} data-testid="plan-lock-seats"
                style={btn({ color: C.text, borderColor: C.steel, fontSize: 11.5, padding: "5px 12px" })}>
                raise the seat limit →
              </button>
            ) : (
            <button onClick={() => navigate("pricing?for=coding")} data-testid="plan-lock-pricing"
              style={btn({ color: C.text, borderColor: C.steel, fontSize: 11.5, padding: "5px 12px" })}>
              Coding plan · $25 / developer / month →
            </button>
            )}
            {reasons.includes("agent_family") && mine && kept && mine !== kept && (
              <button onClick={() => swap.mutate(mine)} disabled={swap.isPending}
                data-testid="plan-lock-keep"
                style={btn({ color: C.mut, fontSize: 11, padding: "5px 12px" })}>
                {swap.isPending ? "…" : `keep ${FAMILY_LABEL[mine]} on the free plan instead`}
              </button>
            )}
          </div>
          {reasons.includes("agent_family") && mine && kept && mine !== kept && (
            <div style={{ fontSize: 10.5, color: C.dim, marginTop: 6 }}>
              then {FAMILY_LABEL[kept] ?? kept} sessions lock instead — nothing is deleted, and you
              can switch back.
            </div>
          )}
          {swap.isError && (
            <div style={{ fontSize: 10.5, color: C.red, marginTop: 6 }}>
              {(swap.error as Error).message} — choosing the family takes a workspace admin
            </div>
          )}
        </>
      )}
    </div>
  );
}

/** One developer's coding sessions, folded: who, which agents, how many
 * sessions, what needs a look — unfold to open one. */
function DeveloperRow({ developer, sessions, rows, st, first, open, onToggle, looks, locked, children }: {
  developer: string; sessions: NodeInfo[]; rows: (FleetRow | undefined)[]; st: Status[]; locked?: LockReason[];
  looks: { label: string; more: number; node: string }[];
  first: boolean; open: boolean; onToggle: () => void; children: React.ReactNode;
}) {
  // a person is as their most urgent session — but one stopped session does
  // not make a developer whose other sessions run look stopped
  const p = st.includes("needs_you") ? "needs_you" : st.includes("running") ? "running" : primary(st);
  const color = STATUS_COLOR[p];
  const sum = (k: "runs" | "calls" | "tainted" | "denied") => rows.reduce((a, r) => a + (r?.[k] ?? 0), 0);
  const agents = [...new Set(sessions.map((s) => s.profile?.agent || (s.node_id.startsWith("codex-") ? "codex" : "claude-code")))]
    .map((a) => (a === "codex" ? "Codex" : "Claude Code")).join(" + ");
  const running = rows.filter((r) => r?.active_run).length;
  const last = rows.map((r) => r?.last_ts ?? "").concat(sessions.map((s) => s.reported?.updated_ts ?? "")).sort().pop();
  const tainted = sum("tainted");
  const denied = sum("denied");
  return (
    <div data-testid={`developer-row-${developer}`} style={{ borderTop: first ? "none" : `1px solid ${C.line}` }}>
      <div onClick={onToggle} className="px-4 py-3" style={{ cursor: "pointer" }}>
        <div className="flex items-center gap-2.5 flex-wrap">
          <Circle size={8} fill={color} color={color} />
          <span style={{ fontFamily: MONO, fontSize: 13, color: C.text, flex: 1, minWidth: 120 }}>{developer}</span>
          {/* past the free plan, the developer is: said on their row, folded or not */}
          {locked && <LockChip reasons={locked} />}
          {/* what needs a look, from any of the sessions — folded, it still says */}
          {looks.length > 0 && (
            <button data-testid={`developer-look-${developer}`}
              onClick={(e) => { e.stopPropagation(); navigate("evidence", looks.length === 1 ? { agent: looks[0].node } : {}); }}
              style={{ fontFamily: MONO, fontSize: 10, color: C.red, background: "none", border: `1px solid ${C.red}`,
                       borderRadius: 20, padding: "1px 8px", cursor: "pointer" }}>
              {looks[0].label}{looks.length + looks.reduce((a, l) => a + l.more, 0) > 1
                ? ` · +${looks.length - 1 + looks.reduce((a, l) => a + l.more, 0)}` : ""} →
            </button>
          )}
          {[...new Map(sessions.flatMap((n) => attention(n)).map((a) => [a.label, a])).values()].map((a) => (
            <button key={a.label}
              onClick={(e) => { if (a.route) { e.stopPropagation(); navigate(a.route); } }}
              style={{ fontFamily: MONO, fontSize: 10, color: a.color, background: "none",
                       border: `1px solid ${a.color}`, borderRadius: 20, padding: "1px 8px",
                       cursor: a.route ? "pointer" : "default" }}>
              {a.label}{a.route ? " →" : ""}
            </button>
          ))}
          <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>{agents}</span>
        </div>
        <div className="flex gap-3 flex-wrap" style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, marginTop: 4, marginLeft: 18 }}>
          {running > 0
            ? <span style={{ color: C.green }}>● {running} running</span>
            : <span>{STATUS_LABEL[p]} · last active {lastActive(last || null)}</span>}
          <span>{sessions.length} session{sessions.length === 1 ? "" : "s"} · last 24h: {sum("calls")} calls</span>
          {tainted > 0 && <span style={{ color: C.amber }}>{tainted} answer{tainted === 1 ? "" : "s"} from outside text</span>}
          {denied > 0 && <span style={{ color: C.red }}>{denied} refused</span>}
          <span style={{ color: C.steel }}>{open ? "▾ hide sessions" : "▸ show sessions"}</span>
        </div>
      </div>
      {open && <div style={{ marginLeft: 22, borderLeft: `1px solid ${C.line}` }}>{children}</div>}
    </div>
  );
}

function TreeGroupRow({ tree, first, open, onToggle, locked, rows, children }: {
  tree: TreeSummary; first: boolean; open: boolean; onToggle: () => void;
  locked?: LockReason[]; rows: (FleetRow | undefined)[];
  children: React.ReactNode;
}) {
  const quiet = !!tree.quiet;
  const color = quiet ? C.amber : tree.status === "running" ? C.green
    : tree.status === "failed" || tree.status === "stopped" ? C.red
    : tree.status === "paused" ? C.amber : C.dim;
  // counted from its runs: how many are at work, done, and did not finish
  const runs = (tree.stages ?? []).flatMap((s) => s.flatMap((n) => n.runs));
  const planned = (tree.stages ?? []).reduce((k, s) => k + s.reduce((j, n) => j + (n.count ?? n.runs.length), 0), 0);
  const working = runs.filter((r) => r.status === "running").length;
  const done = runs.filter((r) => r.status === "ok").length;
  const bad = runs.filter((r) => r.status && r.status !== "ok" && r.status !== "running").length;
  const calls = rows.reduce((k, r) => k + (r?.calls ?? 0), 0);
  const refused = rows.reduce((k, r) => k + (r?.denied ?? 0), 0);
  const progress = treeProgress(tree);
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
            data-testid={`tree-open-${tree.tree_run}`}
            style={{ fontFamily: MONO, fontSize: 10.5, color: C.steel, background: "none",
                     border: "none", cursor: "pointer" }}>
            open tree →
          </button>
        </div>
        <div className="flex gap-3 flex-wrap" data-testid={`tree-counts-${tree.tree_run}`}
          style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, marginTop: 4, marginLeft: 22 }}>
          <span style={{ color }}>{quiet ? "stopped reporting" : tree.status}</span>
          {progress && tree.status === "running" && !quiet && <span>{progress}</span>}
          {runs.length > 0 ? (<>
            {working > 0 && <span style={{ color: C.green }}>{working} working</span>}
            <span>{done} of {Math.max(planned, runs.length)} done</span>
            {bad > 0 && <span style={{ color: C.red }}>{bad} {tree.status === "stopped" ? "stopped" : "not finished"}</span>}
          </>) : <span>agent tree · {tree.agents} agent run{tree.agents === 1 ? "" : "s"}</span>}
          {calls > 0 && <span>{calls} call{calls === 1 ? "" : "s"}</span>}
          {refused > 0 && <span style={{ color: C.red }}>{refused} refused</span>}
          <span style={{ color: C.steel }}>{open ? "▾ hide agents" : "▸ show agents"}</span>
        </div>
      </div>
      {open && <div style={{ marginLeft: 22, borderLeft: `1px solid ${C.line}` }}>{children}</div>}
    </div>
  );
}

/** A tree's agents by stage, in the order they run: the stage, its node and
 *  agent family, then a row per run — or a line for one not started yet. */
function TreeStages({ tree, render }: { tree: TreeSummary; render: (nodeId: string) => React.ReactNode }) {
  const ended = tree.status !== "running" && tree.status !== "paused";
  return (
    <div data-testid={`tree-stages-${tree.tree_run}`}>
      {(tree.stages ?? []).map((stage, k) => (
        <div key={k} style={{ borderTop: k === 0 ? "none" : `1px solid ${C.line}` }}>
          {stage.map((n) => {
            const count = n.count ?? n.runs.length;
            const pending = Math.max(0, count - n.runs.length);
            return (
              <div key={n.node}>
                <div className="px-4" data-testid={`tree-stage-${tree.tree_run}-${n.node}`}
                  style={{ fontFamily: MONO, fontSize: 10, color: C.dim, paddingTop: 8,
                           letterSpacing: "0.04em" }}>
                  {k + 1} · {n.node.toUpperCase()} · {n.agent || "agent"}{count > 1 ? ` ×${count}` : ""}
                  {n.access === "read" ? " · read only" : ""}
                </div>
                {n.runs.map((r, i) => {
                  const row = r.node_id ? render(r.node_id) : null;
                  if (row) return row;
                  const word = r.status === "running" ? "starting…"
                    : r.status === "ok" ? "done" : r.status || (ended ? "never ran" : "waiting");
                  return <PendingRun key={`${n.node}-${i}`}
                    label={count > 1 ? `${n.node} ${r.index + 1} of ${count}` : n.node} word={word} />;
                })}
                {Array.from({ length: pending }, (_, i) => (
                  <PendingRun key={`${n.node}-p${i}`}
                    label={count > 1 ? `${n.node} ${n.runs.length + i + 1} of ${count}` : n.node}
                    word={ended ? "never ran" : "waiting for the stage before"} />
                ))}
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

function PendingRun({ label, word }: { label: string; word: string }) {
  return (
    <div className="px-4 py-2 flex items-center gap-2.5"
      style={{ fontFamily: MONO, fontSize: 11.5, color: C.dim }}>
      <Circle size={8} color={C.line} />
      <span style={{ flex: 1 }}>{label}</span>
      <span style={{ fontSize: 10.5, color: word === "stopped" ? C.red : C.dim }}>{word}</span>
    </div>
  );
}

/** A subagent under its session: one line — what it was asked, what it is
 *  doing, its calls. */
function SubagentRowView({ n, row, st, selected, onPick, locked }: {
  n: NodeInfo; row?: FleetRow; st: Status[]; selected: boolean; onPick: () => void;
  locked?: LockReason[];
}) {
  const p = primary(st);
  // a stop or a pause is what it is now, whatever it was doing a moment ago
  const held = p === "stopped" || p === "paused";
  const word = !held && row?.active_run ? `● ${row.current_tool ?? "running"}` : STATUS_LABEL[p];
  const color = !held && row?.active_run ? C.green : STATUS_COLOR[p];
  return (
    <div onClick={onPick} className="py-2 flex items-center gap-2.5" data-testid={`subagent-row-${n.node_id}`}
      title={n.node_id}
      style={{ cursor: "pointer", fontFamily: MONO, fontSize: 11.5, paddingLeft: 40, paddingRight: 16,
               background: selected ? "rgba(127,168,204,0.05)" : "transparent" }}>
      <span style={{ color: C.dim }}>↳</span>
      <Circle size={7} fill={color} color={color} />
      <span style={{ color: C.text, flex: 1, minWidth: 100, overflow: "hidden", textOverflow: "ellipsis",
                     whiteSpace: "nowrap" }}>
        {n.profile?.title || "subagent"}
        <span style={{ color: C.dim, fontSize: 10.5 }}> · subagent</span>
      </span>
      {locked ? <LockChip reasons={locked} /> : (<>
        <span style={{ color, fontSize: 10.5 }}>{word}</span>
        <span style={{ color: C.mut, fontSize: 10.5, minWidth: 54, textAlign: "right" }}>
          {row?.calls ?? 0} call{row?.calls === 1 ? "" : "s"}
        </span>
        {row && row.denied > 0 && <span style={{ color: C.red, fontSize: 10.5 }}>{row.denied} refused</span>}
      </>)}
    </div>
  );
}

function FleetRowView({ n, row, st, first, selected, onPick, locked, look, member }: {
  n: NodeInfo; row?: FleetRow; st: Status[]; first: boolean; selected: boolean; onPick: () => void;
  locked?: LockReason[]; look?: { label: string; more: number };
  member?: TreeMember;
}) {
  const folded = useApp((s) => s.planNoticesFolded);
  const p = primary(st);
  const color = STATUS_COLOR[p];
  if (member && !locked) {
    // one line per run of a tree: what it is, what it is doing, its counts
    const word = row?.active_run ? `● ${row.current_tool ?? "running"}`
      : member.status === "ok" ? "done"
      : member.status === "running" ? (row?.calls ? "running" : "starting…")
      : member.status || STATUS_LABEL[p];
    const wcolor = row?.active_run ? C.green : member.status === "ok" ? C.mut
      : member.status === "running" ? C.mut : C.red;
    return (
      <div onClick={onPick} className="px-4 py-2 flex items-center gap-2.5" data-testid={`agent-row-${n.node_id}`}
        title={n.node_id}
        style={{ cursor: "pointer", fontFamily: MONO, fontSize: 11.5,
                 background: selected ? "rgba(127,168,204,0.05)" : "transparent" }}>
        <Circle size={8} fill={row?.active_run ? C.green : color} color={row?.active_run ? C.green : color} />
        <span style={{ color: C.text, flex: 1, minWidth: 100 }}>{member.label}</span>
        <span style={{ color: wcolor, fontSize: 10.5 }} data-testid={`member-state-${n.node_id}`}>{word}</span>
        <span style={{ color: C.mut, fontSize: 10.5, minWidth: 54, textAlign: "right" }}>
          {row?.calls ?? 0} call{row?.calls === 1 ? "" : "s"}
        </span>
        {row && row.denied > 0 && <span style={{ color: C.red, fontSize: 10.5 }}>{row.denied} refused</span>}
        {look && <span style={{ color: C.red, fontSize: 10.5 }}>{look.label}</span>}
      </div>
    );
  }
  return (
    <div onClick={onPick} className="px-4 py-3" data-testid={`agent-row-${n.node_id}`}
      style={{ cursor: "pointer", borderTop: first ? "none" : `1px solid ${C.line}`,
               background: selected ? "rgba(127,168,204,0.05)" : "transparent" }}>
      <div className="flex items-center gap-2.5 flex-wrap">
        <Circle size={8} fill={color} color={color} />
        {member ? (
          <span style={{ fontFamily: MONO, fontSize: 12.5, color: locked ? C.mut : C.text, flex: 1, minWidth: 120 }}
            title={n.node_id}>
            {member.label}
            <span style={{ fontSize: 10.5, color: C.dim }}> · {member.agent || n.profile?.agent || "agent"}</span>
          </span>
        ) : n.profile?.title ? (
          <span style={{ fontSize: 13.5, color: locked ? C.mut : C.text, flex: 1, minWidth: 120 }}>
            {n.profile.title}
            <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}> · {n.node_id}</span>
          </span>
        ) : (
          <span style={{ fontFamily: MONO, fontSize: 13, color: locked ? C.mut : C.text, flex: 1, minWidth: 120 }}>{n.node_id}</span>
        )}
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
        {!locked && look && (
          <button data-testid={`agent-look-${n.node_id}`}
            onClick={(e) => { e.stopPropagation(); navigate("evidence", { agent: n.node_id }); }}
            style={{ fontFamily: MONO, fontSize: 10, color: C.red, background: "none", border: `1px solid ${C.red}`,
                     borderRadius: 20, padding: "1px 8px", cursor: "pointer" }}>
            {look.label}{look.more ? ` · +${look.more}` : ""} →
          </button>
        )}
      </div>
      {locked ? (!folded && (
        <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 4, marginLeft: 18 }}>
          protected by its hooks · shown and operated on the Coding plan
        </div>
      )) : (
      <div className="flex gap-3 flex-wrap" style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, marginTop: 4, marginLeft: 18 }}>
        {row?.active_run ? (
          <span style={{ color: C.green, cursor: "pointer" }}
            onClick={(e) => { e.stopPropagation(); navigate(`govern/run/${encodeURIComponent(row.active_run!)}`); }}>
            ● running{row.current_tool ? ` · ${row.current_tool}` : ""} →
          </span>
        ) : member && member.status && member.status !== "running" ? (
          <span style={{ color: member.status === "ok" ? C.mut : C.red }}>
            {member.status === "ok" ? "done" : member.status} · {lastActive(row?.last_ts ?? n.reported?.updated_ts)}
          </span>
        ) : (
          <span>{STATUS_LABEL[p]} · last active {lastActive(row?.last_ts ?? n.reported?.updated_ts)}</span>
        )}
        {row && <span>last 24h: {row.runs} run{row.runs === 1 ? "" : "s"} · {row.calls} call{row.calls === 1 ? "" : "s"}</span>}
        {row && row.tainted > 0 && <span style={{ color: C.amber }}>{row.tainted} answer{row.tainted === 1 ? "" : "s"} from outside text</span>}
        {row && row.denied > 0 && <span style={{ color: C.red }}>{row.denied} refused</span>}
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

/** What a call was about, in a few words: its command, file, pattern or URL. */
function argWords(args: unknown): string {
  if (!args || typeof args !== "object") return "";
  const a = args as Record<string, unknown>;
  if (typeof a.pattern === "string" && a.pattern) {
    return `“${a.pattern.slice(0, 40)}”${typeof a.path === "string" && a.path ? ` in ${a.path.slice(0, 30)}` : ""}`;
  }
  for (const k of ["command", "file_path", "path", "url", "query", "description", "prompt"]) {
    const v = a[k];
    if (typeof v === "string" && v.trim()) return v.replace(/\s+/g, " ").trim().slice(0, 70);
  }
  return "";
}

/** An agent's answer, said as what it is: a plan, findings, a tree's result —
 *  not the JSON it was written in. */
export function answerWords(text: string): string {
  const raw = text.trim();
  if (raw.startsWith("{")) {
    try {
      const d = JSON.parse(raw) as Record<string, unknown>;
      if (d.nodes && typeof d.nodes === "object") {
        const runs = Object.values(d.nodes as Record<string, { status?: string }[]>).flat();
        const ok = runs.filter((r) => r?.status === "ok").length;
        const stopped = runs.filter((r) => r?.status === "stopped").length;
        return `the tree finished: ${ok} of ${runs.length} runs done${stopped ? ` · ${stopped} stopped` : ""}`;
      }
      if (Array.isArray(d.findings)) {
        const first = d.findings[0] as { claim?: string } | undefined;
        return `found ${d.findings.length} thing${d.findings.length === 1 ? "" : "s"}${first?.claim ? `: ${first.claim}` : ""}`;
      }
      if (Array.isArray(d.steps)) {
        return `planned ${d.steps.length} step${d.steps.length === 1 ? "" : "s"}${d.steps[0] ? `: ${String(d.steps[0])}` : ""}`;
      }
      return "answered";
    } catch { /* not JSON after all: said as text */ }
  }
  return raw.split("\n").find((l) => l.trim())?.trim().slice(0, 90) ?? "";
}

function feedLine(e: ActivityEvent, call?: Record<string, unknown>): { text: string; color: string } {
  const p = e.payload;
  const tool = typeof p.tool === "string" ? p.tool : "";
  const what = (args: unknown) => {
    const w = argWords(args);
    return w ? `${tool} · ${w}` : tool;
  };
  switch (e.kind) {
    case "tool_call":
      return p.control ? { text: `${what(p.args)} — held (${String(p.control)})`, color: C.amber }
        : e.verdict === "deny"
        ? { text: `${what(p.args)} — refused${typeof p.reason === "string" && p.reason ? `: ${shortReason(p.reason)}` : ""}`, color: C.red }
        : p.would_deny ? { text: `${what(p.args)} — would have been refused`, color: C.amber }
          : { text: `${what(p.args)} …`, color: C.text };
    case "tool_result": {
      const src = ((p.root as { sources?: string[] } | undefined)?.sources ?? []).join(", ");
      const check = p.check as { kind?: string; passed?: boolean } | undefined;
      const head = what(call?.args);
      if (check?.kind && check.passed === false) return { text: `${head} — the ${check.kind} failed`, color: C.red };
      if (p.status && p.status !== "ok") return { text: `${head} — ${String(p.status)}`, color: C.amber };
      return src ? { text: `${head} · text from outside (${src.replace("unknown_external", "outside")})`, color: C.amber }
        : { text: head, color: C.text };
    }
    case "claim": return { text: answerWords(String(p.text ?? "")), color: C.mut };
    case "fault_injected": return { text: `${tool} broken on purpose (chaos test)`, color: C.steel };
    case "message_sent": {
      const to = String(p.to ?? "");
      if (e.verdict === "deny") return { text: `message to ${to} — refused`, color: C.red };
      const carried = ((p.carried as { root?: { sources?: string[] } } | undefined)?.root?.sources ?? []);
      return carried.length
        ? { text: `sent a message to ${to} · carries text from outside (${carried.join(", ")})`, color: C.amber }
        : { text: `sent a message to ${to}`, color: C.text };
    }
    case "message_received": return { text: `got a message from ${String(p.from ?? "")}`, color: C.text };
    case "node_spawned": return { text: `started ${String(p.role || p.child_id || "an agent")}`, color: C.mut };
    case "denial": return { text: `${tool} — refused`, color: C.red };
    case "fact": return { text: `fact ${String(p.fact_type ?? "")}`, color: C.amber };
    default: {
      const t = describeAck(e.kind, p);
      return { text: t, color: e.kind.endsWith("refused") ? C.red : C.steel };
    }
  }
}

interface FeedRow { e: ActivityEvent; text: string; color: string }

/** The feed's rows: a call and its answer are one row (the answer's), a tree's
 *  burst of "started" lines is one row naming its nodes. Newest first. */
export function feedRows(events: ActivityEvent[]): FeedRow[] {
  const key = (e: ActivityEvent, id: unknown) => `${e.run_id}\u0000${String(id ?? "")}`;
  const calls = new Map<string, Record<string, unknown>>();
  const answered = new Set<string>();
  for (const e of events) {
    if (e.kind === "tool_call" && e.payload.tool_call_id) calls.set(key(e, e.payload.tool_call_id), e.payload);
    if (e.kind === "tool_result" && e.payload.value_ref) answered.add(key(e, e.payload.value_ref));
  }
  const out: FeedRow[] = [];
  for (let i = 0; i < events.length; i++) {
    const e = events[i];
    if (e.kind === "tool_call" && e.verdict !== "deny" && !e.payload.control
        && answered.has(key(e, e.payload.tool_call_id))) continue;
    if (e.kind === "node_spawned") {
      const burst = [e];
      while (events[i + 1]?.kind === "node_spawned" && events[i + 1].run_id === e.run_id) burst.push(events[++i]);
      if (burst.length > 1) {
        const roles = new Map<string, number>();
        for (const b of burst) {
          const r = String(b.payload.role || "agent");
          roles.set(r, (roles.get(r) ?? 0) + 1);
        }
        const names = [...roles].reverse().map(([r, k]) => (k > 1 ? `${r} ×${k}` : r)).join(", ");
        out.push({ e, text: `started ${burst.length} agents: ${names}`, color: C.mut });
        continue;
      }
    }
    const call = e.kind === "tool_result" ? calls.get(key(e, e.payload.value_ref)) : undefined;
    out.push({ e, ...feedLine(e, call) });
  }
  return out;
}

function ActivityFeed({ only }: { only?: Set<string> }) {
  const feed = useQuery({ queryKey: ["activity"], queryFn: api.activity, refetchInterval: 2000 });
  const nodesQ = useQuery({ queryKey: ["nodes"], queryFn: api.nodes });
  const treesQ = useQuery({ queryKey: ["trees"], queryFn: api.trees });
  const examples = new Set((nodesQ.data ?? []).filter((n) => n.example_hidden).map((n) => n.node_id));
  // a coding session reads as its task: the developer's first request in it;
  // a tree's agent as its node ("read 3 of 6"); a tree as its task
  const titleOf = new Map((nodesQ.data ?? []).filter((n) => n.profile?.title)
    .map((n) => [n.node_id, n.profile!.title!]));
  for (const [id, m] of treeMembers(treesQ.data?.trees)) titleOf.set(id, m.label);
  for (const t of treesQ.data?.trees ?? []) if (t.task) titleOf.set(t.tree_run, `tree: ${t.task}`);
  // a session past the free plan is not shown, here either
  const ent = useQuery({ queryKey: ["entitlements"], queryFn: api.entitlements, retry: false });
  const [find, setFind] = useState("");
  const kept = (feed.data?.events ?? []).filter((e) => {
    if (ent.data?.locked[e.node_id] || ent.data?.locked_trees[e.node_id]) return false;
    // a skill's dry run is shown on Try a skill, not among the agents' work
    if (e.node_id.startsWith("skill-try.")) return false;
    if (examples.has(e.node_id)) return false;
    if (only && !only.has(e.node_id)) return false;
    return true;
  });
  const rows = feedRows(kept).filter(({ e, text }) => !find
    || `${e.node_id} ${titleOf.get(e.node_id) ?? ""} ${text}`.toLowerCase().includes(find.toLowerCase()));
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
        {rows.length === 0 && (
          <div className="p-3" style={{ color: C.dim }}>
            {feed.isLoading ? "loading…" : "nothing yet — agent steps and operator acks land here as they happen."}
          </div>
        )}
        {rows.map(({ e, text, color }, i) => {
          // agent work opens its run; an ack opens the agent it came from
          const go = () => work.has(e.kind)
            ? navigate(`govern/run/${encodeURIComponent(e.run_id)}`)
            : navigate(`control/${encodeURIComponent(e.node_id)}`);
          const who = titleOf.get(e.node_id) ?? e.node_id;
          return (
            <div key={i} onClick={go} className="px-3 py-1.5 flex gap-2" data-testid="feed-row"
              title={`${who} (${e.node_id}) — ${text}`}
              style={{ borderTop: i === 0 ? "none" : `1px solid ${C.line}`, cursor: "pointer",
                       whiteSpace: "nowrap", minWidth: 0 }}>
              <span style={{ color: C.dim }}>{e.ts ? stamp(e.ts) : ""}</span>
              <span style={{ color: C.steel, maxWidth: "38%", overflow: "hidden", textOverflow: "ellipsis",
                             flexShrink: 0 }}>{who}</span>
              <span style={{ color, overflow: "hidden", textOverflow: "ellipsis", minWidth: 0 }}>{text}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** Whether this workspace's own agents report here — then the examples go. */
export function hasRealWork(nodes: { node_id: string; example?: boolean }[]): boolean {
  return nodes.some((n) => !n.example && !n.node_id.startsWith("skill-try."));
}

/** The refusal the agent was told, cut to its first clause. */
function shortReason(reason: string): string {
  return refusalWords(reason).split(/ — |\. /)[0].slice(0, 90);
}
