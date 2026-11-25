// The production way in that governs: wrap the agent in code. Pick the
// framework, let the scanner (the Config Builder's engine, axor-wrap on the
// backend) find the agent's tools and guess what each does, correct it, then a
// key and a snippet built from exactly those tools — and wait for the agent to
// report in. The tool classes are what lets a call be refused: a value from a
// tool that brings in outside text, driving a tool that sends something out, is
// denied. Without them the wrap only pauses and stops.
import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Check, Copy, Download, Upload } from "lucide-react";
import { api, type WrapTool } from "../api";
import { navigate } from "../router";
import { isGuestToken, useApp } from "../store";
import { untrustedFields } from "../policyConfig";
import { C, MONO, btn } from "../theme";
import { IdentityLogin } from "../tabs/Settings";
import { filesFromList, plainGuess } from "../tabs/ConfigBuilder";

export type Framework = "langchain" | "claude" | "python";
type Klass = "READ" | "WRITE" | "EXPORT" | "EXEC";

const FRAMEWORKS: { id: Framework; label: string; points: string[] }[] = [
  {
    id: "langchain", label: "LangChain",
    points: [
      "your tools are checked before every call — one fed by outside text cannot send it out",
      "pause, stop and a call budget from the plane hold every model and tool call",
      "cut a bad tool result from the agent's context, live",
      "optional: [security] adds reputation across runs, probe=True a health check after each run (costs model calls)",
    ],
  },
  {
    id: "claude", label: "Claude Agent (axor-claude)",
    points: [
      "the session runs on axor-core: its own read / write / bash / search tools are already governed",
      "pause, stop and a call budget from the plane; cut a tool result from the conversation",
      "optional: [security] adds reputation across runs, probe=True a health check (costs model calls)",
    ],
  },
  {
    id: "python", label: "Plain Python",
    points: [
      "your tool functions are checked before every call — one fed by outside text cannot send it out",
      "pause and stop from the plane hold its tool calls",
      "each run posted with its answer: a claimed refund that failed shows as a caught lie",
      "cutting context needs the agent loop in axor-core — use LangChain or the Claude adapter for that",
    ],
  },
];

const CLASSES: { id: Klass; label: string }[] = [
  { id: "READ", label: "reads" },
  { id: "WRITE", label: "changes something" },
  { id: "EXPORT", label: "sends out" },
  { id: "EXEC", label: "runs code" },
];

interface Row {
  tool: WrapTool;
  klass: Klass;
  outside: boolean; // brings in outside text (web, email, documents)
}

const MANIFESTS_FILE = "axor.manifests.json";

export function adapterSnippet(
  framework: Framework,
  o: { origin: string; nodeId: string; key: string | null; tools: string[]; manifestsFile: boolean },
): string {
  // an open plane (auth off) takes no key: no line for one
  const env = [`export AXOR_PLANE_URL=${o.origin}`, `export AXOR_NODE_ID=${o.nodeId}`,
    o.key !== null ? `export AXOR_INGEST_KEY=${o.key}` : null].filter(Boolean).join("\n");
  if (framework === "claude") {
    return `pip install 'axor-claude[plane]'

${env}

import axor_claude

session = axor_claude.make_session()        # live on this plane
await session.run("...")`;
  }
  const names = o.tools.length ? o.tools : ["search_docs", "send_email"];
  const dict = `{${names.map((n) => `"${n}": ${n}`).join(", ")}}`;
  const load = o.manifestsFile
    ? `manifests = json.load(open("${MANIFESTS_FILE}"))["manifests"]   # from step 2`
    : `# axor-wrap manifest ./your_agent -o .axor   — finds the tools, one file each
manifests = [json.loads(p.read_text()) for p in pathlib.Path(".axor").glob("*.manifest.json")]`;
  const imports = o.manifestsFile ? "import json" : "import json, pathlib";
  if (framework === "langchain") {
    return `pip install 'axor-langchain[plane]' axor-wrap

${env}

${imports}
from langchain.agents import create_agent
from langchain_core.tools import tool
from axor_langchain import AxorMiddleware
from axor_wrap.runtime import wrap_callables

${load}
# your tool functions (for @tool ones, pass search_docs.func)
checked = wrap_callables(${dict}, manifests)

agent = create_agent(model, [tool(f) for f in checked.values()],
                     middleware=[AxorMiddleware()])   # pause / stop / budget from the plane`;
  }
  const last = names[names.length - 1];
  return `pip install 'axor-wrap[plane]'

${env}

${imports}, asyncio, os, uuid
from axor_wrap.runtime import WrappedToolset
from axor_wrap.connect import PlaneConnector
from axor_wrap.observe import Experiment

${load}
node = PlaneConnector(os.environ["AXOR_PLANE_URL"], os.environ["AXOR_NODE_ID"],
                      ingest_key=os.environ.get("AXOR_INGEST_KEY"),
                      test_bench=os.environ.get("AXOR_TEST_BENCH") == "1").connect()
asyncio.create_task(node.run())     # keeps it live on the plane (inside your event loop)
asyncio.create_task(node.post_profile("agent", agent="axor-wrap"))   # what it is, test bench or not

async def handle(request: str) -> str:
    run_id = f"{node.session.node_id}-{uuid.uuid4().hex[:10]}"
    # a tool the plane asked to break on this run — only on a test bench (AXOR_TEST_BENCH=1,
    # a copy with no real users; pip install axor-eval)
    test = node.session.take_pending_experiment()
    # one toolset per request: the task is what the user asked, so values from it are trusted
    toolset = node.gate(WrappedToolset(${dict}, manifests,
                                       node_id=node.session.node_id, task=request,
                                       experiment=Experiment(test["faults"], seed=run_id) if test else None))
    out = toolset.call("${last}", {...})   # or hand toolset.callables() to your loop
    answer = ...                           # what your agent tells the user
    # the run on the plane: every call, its verdict, and the answer it is audited against
    await node.post_ingest(toolset.run_lines(answer), run_id)
    return answer

# optional: reputation across runs (pip install axor-sentinel) and a health check
# after a run (pip install axor-probe; costs model calls) — ReputationBook and
# HealthCheck in axor_wrap.observe, posted with node.post_reputation / post_health_check`;
}

const step = (n: number, text: string) => (
  <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, letterSpacing: "0.06em", margin: "18px 0 6px" }}>
    {n} · {text}
  </div>
);
const hint = { fontFamily: MONO, fontSize: 10.5, color: C.dim, lineHeight: 1.6 } as const;

export default function AdapterOnboarding() {
  const [framework, setFramework] = useState<Framework>("langchain");
  // what Policy (or an earlier scan) already found starts here: the same
  // record, nothing to scan or classify twice
  const [rows, setRows] = useState<Row[]>(() =>
    Object.entries(useApp.getState().toolPolicy)
      .filter(([, c]) => c.source && c.args_schema)
      .map(([id, c]) => ({
        tool: {
          id, source: c.source ?? "", description: "", args_schema: c.args_schema ?? { type: "object" },
          framework: "", schema_confidence: "",
          guess: { default_class: c.klass, driving_args: c.driving ?? [],
                   untrusted_fields: untrustedFields(c), confidence: "high", reason: "" },
        },
        klass: c.klass,
        outside: c.outside,
      })));
  const [scanErr, setScanErr] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);
  const [saved, setSaved] = useState(false);
  const [nodeId, setNodeId] = useState("agent-1");
  const [key, setKey] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const connect = useApp((s) => s.connect);
  // the shared record of the agent's tools: a choice made here shows in
  // Policy, in the proxy's onboarding and in a test run
  const policy = useApp((s) => s.toolPolicy);
  const setToolClass = useApp((s) => s.setToolClass);
  const email = useApp((s) => s.identityEmail);
  const token = useApp((s) => s.apiToken);
  const auth = useQuery({ queryKey: ["auth-status"], queryFn: api.authStatus, staleTime: 60_000 });
  const authOn = auth.data?.auth_enabled ?? null;
  const signedIn = !!email || (!!token && !isGuestToken(token));
  const nodes = useQuery({ queryKey: ["nodes"], queryFn: api.nodes, refetchInterval: 3000 });
  // the agents already on the plane when this page opened: a name among them is
  // another agent's, and "reported in" for it would be that agent reporting
  const [takenAtOpen, setTakenAtOpen] = useState<Set<string> | null>(null);
  useEffect(() => {
    if (takenAtOpen === null && nodes.data) setTakenAtOpen(new Set(nodes.data.map((n) => n.node_id)));
  }, [nodes.data, takenAtOpen]);
  const taken = takenAtOpen?.has(nodeId.trim()) ?? false;
  const live = !taken && (nodes.data ?? []).some((n) => n.node_id === nodeId.trim());

  const mint = useMutation({
    mutationFn: () => api.createKey(["ingest"], `node ${nodeId}`, nodeId),
    onSuccess: (k) => setKey(k.secret),
  });
  const download = useMutation({
    mutationFn: () => api.wrapManifests(rows.map((r) => ({
      id: r.tool.id, source: r.tool.source, description: r.tool.description,
      args_schema: r.tool.args_schema, framework: r.tool.framework,
      schema_confidence: r.tool.schema_confidence,
      effect: {
        default_class: r.klass,
        driving_args: r.tool.guess.driving_args,
        untrusted_fields: untrustedFields({ outside: r.outside, outsideFields: policy[r.tool.id]?.outsideFields }),
      },
    }))),
    onSuccess: (bundle) => {
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([JSON.stringify(bundle, null, 2)], { type: "application/json" }));
      a.download = MANIFESTS_FILE;
      a.click();
      setSaved(true);
    },
  });

  const scan = async (files: FileList | null) => {
    setScanErr(null);
    setSaved(false);
    const picked = await filesFromList(files);
    if (picked.length === 0) { setScanErr("no .py files — the scanner reads Python sources"); return; }
    setScanning(true);
    try {
      const res = await api.wrapScan(picked);
      const next = res.tools.map((t) => ({
        tool: t,
        // a tool already classified (here, in Policy, for the proxy) keeps that call
        // unsure is "changes something", not "reads": a wrong "reads" lets outside text drive it
        klass: policy[t.id]?.klass ?? (t.guess.default_class === "UNKNOWN" ? "WRITE" : t.guess.default_class),
        outside: policy[t.id]?.outside ?? t.guess.untrusted_fields.length > 0,
      }));
      setRows(next);
      // the agent's name from its file, unless one was typed that no agent has yet
      const stem = picked[0]?.path.split("/").pop()?.replace(/\.py$/, "").replace(/[^a-zA-Z0-9]+/g, "-").toLowerCase();
      if (stem && stem !== "main" && stem !== "agent" && (nodeId === "agent-1" || takenAtOpen?.has(nodeId.trim()))) {
        setNodeId(stem);
        setKey(null);
      }
      for (const r of next) {
        setToolClass(r.tool.id, { klass: r.klass, outside: r.outside, source: r.tool.source,
                                  args_schema: r.tool.args_schema, driving: r.tool.guess.driving_args });
      }
      if (res.tools.length === 0) setScanErr("no tools found in those files — the scanner finds LangChain @tool / Tool(...), FastMCP @mcp.tool(), {name, description, input_schema} tool lists, and a plain-Python dict of your tool functions (TOOLS = {name: function}). Tools your agent calls over HTTP by URL are not in code it can see: add each one by hand");
    } catch (e) {
      const msg = (e as Error).message;
      setScanErr(msg.startsWith("501")
        ? "the scanner is not installed on this plane — use the command line below instead"
        : msg);
    } finally {
      setScanning(false);
    }
  };
  const patch = (i: number, p: Partial<Row>) => {
    const next = rows.map((r, j) => (j === i ? { ...r, ...p } : r));
    setRows(next);
    setToolClass(next[i].tool.id, { klass: next[i].klass, outside: next[i].outside });
    setSaved(false);
  };

  const needsKey = authOn !== false;
  const snippet = adapterSnippet(framework, {
    origin: window.location.origin, nodeId: nodeId || "agent-1",
    key: needsKey ? (key ?? "<create the key above>") : null,
    tools: rows.map((r) => r.tool.id), manifestsFile: saved,
  });
  const fw = FRAMEWORKS.find((f) => f.id === framework)!;

  return (
    <div data-testid="adapter-onboarding">
      <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 4px" }}>Wrap your agent</h1>
      <div style={{ fontSize: 13.5, color: C.mut, lineHeight: 1.6 }}>
        A few lines in your agent. Then its tool calls are checked before they run, and you can
        pause, stop and repair it live from this plane.
      </div>

      {step(1, "FRAMEWORK")}
      <div className="flex gap-2 flex-wrap" data-testid="framework-picker">
        {FRAMEWORKS.map((f) => (
          <button key={f.id} onClick={() => setFramework(f.id)}
            style={btn({ color: framework === f.id ? C.text : C.dim,
                         borderColor: framework === f.id ? C.steel : C.line, fontSize: 11.5, padding: "6px 12px" })}>
            {framework === f.id ? "✓ " : ""}{f.label}
          </button>
        ))}
      </div>
      <ul data-testid="framework-points" style={{ ...hint, margin: "8px 0 0", paddingLeft: 16 }}>
        {fw.points.map((p) => <li key={p}>{p}</li>)}
      </ul>

      {framework !== "claude" && (
        <>
          {step(2, "YOUR TOOLS — WHAT EACH ONE DOES")}
          <div style={{ ...hint, color: C.mut, marginBottom: 8 }}>
            Pick your agent's Python files: Axor finds its tools and guesses what each does — you
            correct it. Mark the tools that bring in outside text (web, email, documents): a value
            from them cannot drive a tool that sends something out.
          </div>
          <label style={btn({ color: C.steel, borderColor: C.steel, fontSize: 11.5, padding: "6px 12px", cursor: "pointer" })}>
            <Upload size={12} /> {scanning ? "reading…" : "Choose .py files"}
            <input type="file" multiple accept=".py" data-testid="adapter-files" style={{ display: "none" }}
              onChange={(e) => { const input = e.currentTarget; void scan(input.files).finally(() => { input.value = ""; }); }} />
          </label>
          {scanErr && <div style={{ ...hint, color: C.amber, marginTop: 6 }}>{scanErr}</div>}
          {rows.length > 0 && (
            <div data-testid="adapter-tools" style={{ marginTop: 10, border: `1px solid ${C.line}`, borderRadius: 8 }}>
              {rows.map((r, i) => (
                <div key={r.tool.id} className="px-3 py-2" data-testid={`adapter-tool-${r.tool.id}`}
                  style={{ borderTop: i ? `1px solid ${C.line}` : "none", fontFamily: MONO, fontSize: 11.5 }}>
                  <div className="flex items-center gap-3 flex-wrap">
                    <span style={{ color: C.text, minWidth: 130 }}>{r.tool.id}</span>
                    <select value={r.klass} onChange={(e) => patch(i, { klass: e.target.value as Klass })}
                      aria-label={`what ${r.tool.id} does`}
                      style={{ background: C.bg, color: C.text, border: `1px solid ${C.line}`, borderRadius: 4,
                               fontFamily: MONO, fontSize: 11, padding: "3px 6px" }}>
                      {CLASSES.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
                    </select>
                    <label title="its answers come from outside (web, email, files): they cannot drive a send, write or run" style={{ color: C.mut, display: "flex", alignItems: "center", gap: 5, cursor: "pointer" }}>
                      <input type="checkbox" checked={r.outside} onChange={(e) => patch(i, { outside: e.target.checked })} />
                      brings in outside text
                    </label>
                  </div>
                  <div style={{ ...hint, marginTop: 2 }}>{r.tool.guess.reason ? plainGuess(r.tool.guess.reason, r.tool.guess.confidence) : "from Policy"} · {r.tool.source}</div>
                </div>
              ))}
              <div className="px-3 py-2" style={{ borderTop: `1px solid ${C.line}` }}>
                <button onClick={() => download.mutate()} disabled={download.isPending} data-testid="adapter-download"
                  style={btn({ color: C.text, borderColor: C.steel, fontSize: 11.5, padding: "5px 12px" })}>
                  {saved ? <Check size={12} /> : <Download size={12} />} {saved ? `saved ${MANIFESTS_FILE}` : `Save ${MANIFESTS_FILE}`}
                </button>
                <span style={{ ...hint, marginLeft: 10 }}>put it next to your agent</span>
                {download.isError && <div style={{ ...hint, color: C.red }}>{(download.error as Error).message}</div>}
              </div>
            </div>
          )}
          <div style={{ ...hint, marginTop: 8 }}>
            or on your machine: <span style={{ color: C.mut }}>axor-wrap manifest ./your_agent -o .axor</span> — same
            scan, its guesses uncorrected. Change what a tool may do later under Policy.
          </div>
        </>
      )}

      {step(framework === "claude" ? 2 : 3, "CONNECT IT TO THIS WORKSPACE")}
      {needsKey && authOn && !signedIn ? (
        <div data-testid="adapter-signin" style={{ border: `1px solid ${C.line}`, borderRadius: 8, padding: 14 }}>
          <div style={{ ...hint, color: C.mut, fontSize: 11, marginBottom: 10 }}>
            Your agent reports into your workspace, so this step needs an account — free, a minute.
            Then its key is one click, right here.
          </div>
          <IdentityLogin initialMode="signup" />
        </div>
      ) : (
        <div className="flex items-center gap-2 flex-wrap">
          <span style={{ ...hint, color: C.mut, fontSize: 11 }}>agent name</span>
          <input value={nodeId} onChange={(e) => { setNodeId(e.target.value); setKey(null); }}
            aria-label="agent name"
            style={{ background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: C.text,
                     fontFamily: MONO, fontSize: 11.5, padding: "4px 8px", width: 160 }} />
          {needsKey && !key && (
            <button onClick={() => mint.mutate()} disabled={!nodeId.trim() || mint.isPending} data-testid="adapter-mint"
              style={btn({ color: C.steel, borderColor: C.steel, fontSize: 11, padding: "4px 10px" })}>
              {mint.isPending ? "…" : "create the key"}
            </button>
          )}
          {key && <span style={{ ...hint, color: C.amber }}>the key is shown once — copy it now</span>}
          {!needsKey && <span style={hint}>this plane is open — no key needed</span>}
          {mint.isError && <div style={{ ...hint, color: C.red, width: "100%" }}>{(mint.error as Error).message}</div>}
        </div>
      )}

      {step(framework === "claude" ? 3 : 4, "IN YOUR AGENT")}
      <div className="flex items-start gap-2" style={{ background: C.bg, border: `1px solid ${C.line}`, borderRadius: 6, padding: "8px 10px" }}>
        <pre data-testid="adapter-snippet" style={{ margin: 0, flex: 1, fontFamily: MONO, fontSize: 11.5, color: C.text,
                                                   whiteSpace: "pre-wrap", lineHeight: 1.6 }}>{snippet}</pre>
        <button aria-label="copy" onClick={() => { void navigator.clipboard?.writeText(snippet); setCopied(true); setTimeout(() => setCopied(false), 1200); }}
          style={btn({ color: C.mut, fontSize: 10, padding: "2px 6px" })}>
          {copied ? <Check size={11} /> : <Copy size={11} />}
        </button>
      </div>

      {step(framework === "claude" ? 4 : 5, "RUN IT")}
      <div data-testid="adapter-live" className="p-3" style={{ background: C.panel, borderRadius: 8, fontFamily: MONO, fontSize: 11.5,
        border: `1px solid ${live ? C.green : C.line}` }}>
        {live
          ? <span style={{ color: C.green }}>● {nodeId} reported in — it is on the plane</span>
          : taken
          ? <span data-testid="adapter-name-taken" style={{ color: C.amber }}>
              {nodeId} is already on this plane — if this is that agent, its new manifests take effect on restart;
              a different agent needs its own name, or their runs mix
            </span>
          : <span style={{ color: C.mut }}>waiting for <span style={{ color: C.text }}>{nodeId || "agent-1"}</span> to report in — run your agent…</span>}
      </div>
      <div className="flex gap-3 mt-4 items-center">
        {live ? (
          <button onClick={() => { connect("adapter", []); navigate(`govern/${encodeURIComponent(nodeId)}`); }}
            data-testid="adapter-done"
            style={btn({ color: C.text, borderColor: C.green, fontSize: 12, padding: "7px 16px" })}>
            See it in Agents →
          </button>
        ) : (
          <button onClick={() => navigate("govern")}
            style={{ background: "none", border: "none", padding: 0, cursor: "pointer", fontFamily: MONO, fontSize: 11, color: C.dim }}>
            skip — I'll run it later →
          </button>
        )}
      </div>
    </div>
  );
}
