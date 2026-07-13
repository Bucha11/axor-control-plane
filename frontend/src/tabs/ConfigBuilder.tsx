// Config builder: entry → build → preview (config-builder mockup). The code
// upload is simulated; the config download is real (Blob + anchor click).
import { useState } from "react";
import { Plus, ChevronDown, ChevronRight, Download, Check, X, ArrowRight, Upload, FileCode, Terminal, AlertTriangle } from "lucide-react";
import { C, MONO, btn } from "../theme";
import Coach from "../components/Coach";
import Tooltip from "../components/Tooltip";

const TYPES = ["READ", "WRITE", "EXPORT", "EXEC"] as const;
type ToolType = (typeof TYPES)[number];
type SinkType = ToolType | "?";

interface ArgAllow {
  arg: string;
  set: string[];
}

interface Sink {
  name: string;
  src?: string;
  endpoint?: string;
  type: SinkType;
  critical: boolean;
  args: ArgAllow[];
}

type PeerLevel = "L0" | "L1" | "L2";

// Inter-federation peers are declared like sinks (spec v2 Ch.1 §3):
// identity (pubkey), level, allowed message classes; undeclared = L0.
interface PeerDecl {
  peer_id: string;
  pubkey: string;
  level: PeerLevel;
  classes: string;      // comma-separated message classes (L2 discount scope)
  attested: boolean;    // governance_attested (kernel+config-hash signature)
}

type Stage = "entry" | "analyzing" | "build" | "preview";

const TYPE_COLOR: Record<SinkType, string> = {
  READ: C.green, WRITE: C.amber, EXPORT: C.red, EXEC: C.red, "?": C.amber,
};
const typeColor = (t: SinkType): string => TYPE_COLOR[t];

function Fold({ label, children, openDefault }: {
  label: string; children: React.ReactNode; openDefault?: boolean;
}) {
  const [open, setOpen] = useState(!!openDefault);
  return (
    <div>
      <button onClick={() => setOpen(!open)} style={{ background: "none", border: "none", color: C.mut, fontSize: 12, fontFamily: MONO, cursor: "pointer", display: "flex", alignItems: "center", gap: 6, padding: "8px 0" }}>
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />} {label}
      </button>
      {open && <div className="pb-2">{children}</div>}
    </div>
  );
}

const DETECTED: Sink[] = [
  { name: "web_search", src: "tools.py:14 · @tool", endpoint: "https://api.search.example/v1", type: "?", critical: false, args: [] },
  { name: "send_report", src: "tools.py:31 · @tool", endpoint: "https://slack.example/api/post", type: "?", critical: false, args: [{ arg: "channel", set: ["#reports", "#alerts"] }] },
  { name: "run_query", src: "db.py:8 · langchain Tool()", endpoint: "postgres://…", type: "?", critical: false, args: [] },
  { name: "shell", src: "agent.py:52 · subprocess", endpoint: "local://bash", type: "?", critical: false, args: [] },
];

// ---- per-sink allowlist editor ----
function ArgEditor({ sink, update }: { sink: Sink; update: (p: Partial<Sink>) => void }) {
  const [newArg, setNewArg] = useState("");
  const [addingArg, setAddingArg] = useState(false);
  const [valDrafts, setValDrafts] = useState<Record<string, string>>({}); // argName -> current input

  const addArg = () => {
    if (!newArg.trim()) return;
    update({ args: [...sink.args, { arg: newArg.trim(), set: [] }] });
    setNewArg(""); setAddingArg(false);
  };
  const addVal = (argName: string) => {
    const v = (valDrafts[argName] || "").trim();
    if (!v) return;
    update({ args: sink.args.map((a) => (a.arg === argName ? { ...a, set: [...a.set, v] } : a)) });
    setValDrafts({ ...valDrafts, [argName]: "" });
  };
  const rmVal = (argName: string, v: string) =>
    update({ args: sink.args.map((a) => (a.arg === argName ? { ...a, set: a.set.filter((x) => x !== v) } : a)) });
  const rmArg = (argName: string) => update({ args: sink.args.filter((a) => a.arg !== argName) });

  return (
    <div className="flex flex-col gap-2">
      <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, letterSpacing: "0.08em" }}>ARGUMENT ALLOWLISTS</div>
      {sink.args.length === 0 && (
        <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.dim }}>
          None declared — every argument stays under full taint (safe default).
        </div>
      )}
      {sink.args.map((a) => (
        <div key={a.arg} className="flex items-center gap-2 flex-wrap">
          <span style={{ fontFamily: MONO, fontSize: 11.5, color: C.text }}>{a.arg} ∈</span>
          {a.set.map((v) => (
            <span key={v} className="flex items-center gap-1 px-2 py-0.5" style={{ background: C.panel2, border: `1px solid ${C.line}`, borderRadius: 10, fontFamily: MONO, fontSize: 11, color: C.green }}>
              {v}
              <X size={10} color={C.dim} style={{ cursor: "pointer" }} onClick={() => rmVal(a.arg, v)} />
            </span>
          ))}
          <input value={valDrafts[a.arg] || ""} onChange={(e) => setValDrafts({ ...valDrafts, [a.arg]: e.target.value })}
            onKeyDown={(e) => e.key === "Enter" && addVal(a.arg)} placeholder="+ value ⏎"
            style={{ width: 90, background: "none", border: "none", borderBottom: `1px solid ${C.line}`, color: C.text, fontFamily: MONO, fontSize: 11, padding: "2px 4px", outline: "none" }} />
          <span onClick={() => rmArg(a.arg)} style={{ fontFamily: MONO, fontSize: 10, color: C.dim, cursor: "pointer" }}>remove</span>
          {a.set.length > 0 && <span style={{ fontFamily: MONO, fontSize: 10, color: C.dim }}>— supersession enabled for this argument</span>}
          {a.set.length === 0 && <span style={{ fontFamily: MONO, fontSize: 10, color: C.amber }}>— empty set: still fully tainted</span>}
        </div>
      ))}
      {addingArg ? (
        <div className="flex items-center gap-2">
          <input autoFocus value={newArg} onChange={(e) => setNewArg(e.target.value)} onKeyDown={(e) => e.key === "Enter" && addArg()}
            placeholder="argument name ⏎"
            style={{ width: 160, background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: C.text, fontFamily: MONO, fontSize: 11.5, padding: "5px 8px", outline: "none" }} />
          <button onClick={addArg} style={btn({ padding: "4px 8px" })}><Check size={12} /></button>
        </div>
      ) : (
        <button onClick={() => setAddingArg(true)} style={{ background: "none", border: "none", color: C.steel, fontFamily: MONO, fontSize: 11.5, cursor: "pointer", padding: 0, textAlign: "left" }}>
          + declare argument
        </button>
      )}
    </div>
  );
}

function Container({ children }: { children: React.ReactNode }) {
  return <div style={{ maxWidth: 680, margin: "0 auto" }}>{children}</div>;
}

export default function ConfigBuilder() {
  const [stage, setStage] = useState<Stage>("entry");
  const [sinks, setSinks] = useState<Sink[]>([]);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState<{ name: string; type: ToolType }>({ name: "", type: "READ" });
  const [sel, setSel] = useState<number | null>(null);
  const [emitted, setEmitted] = useState(false);
  const [budgetCapCalls, setBudgetCapCalls] = useState<number | null>(null);
  const [peers, setPeers] = useState<PeerDecl[]>([]);
  const [peerDraft, setPeerDraft] = useState<PeerDecl | null>(null);

  const upload = () => {
    setStage("analyzing");
    setTimeout(() => {
      setSinks(DETECTED.map((d) => ({ ...d, args: d.args.map((a) => ({ ...a, set: [...a.set] })) })));
      setStage("build");
    }, 1100);
  };
  const patch = (i: number, p: Partial<Sink>) => setSinks(sinks.map((s, j) => (j === i ? { ...s, ...p } : s)));
  const unclassified = sinks.filter((s) => s.type === "?").length;
  const fromCode = sinks.some((s) => s.src);

  const config = {
    version: "axor-config/1",
    sinks: Object.fromEntries(sinks.filter((s) => s.type !== "?").map((s) => [s.name, {
      consequence_class: s.type,
      ...(s.critical ? { criticality: "critical" } : {}),
      ...(s.args.some((a) => a.set.length) ? { trusted_sets: Object.fromEntries(s.args.filter((a) => a.set.length).map((a) => [a.arg, a.set])) } : {}),
    }])),
    default: "DENY",
    ...(budgetCapCalls !== null ? { budget_cap_calls: budgetCapCalls } : {}),
    ...(peers.length ? {
      peers: Object.fromEntries(peers.map((p) => [p.peer_id, {
        pubkey: p.pubkey,
        level: p.level.toLowerCase(),
        message_classes: p.classes.split(",").map((c) => c.trim()).filter(Boolean),
        ...(p.attested ? { governance_attested: true } : {}),
      }])),
    } : {}),
  };

  const download = () => {
    const blob = new Blob([JSON.stringify(config, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "axor.config.json";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    setEmitted(true);
  };

  if (stage === "entry" || stage === "analyzing") {
    return (
      <Container>
        <Coach id="config-builder" title="Config Builder — declare what your agent may touch">
          Governance starts from a config: every tool is a{" "}
          <span style={{ color: C.text }}>sink</span> with a consequence class
          (READ / WRITE / EXPORT / EXEC); anything undeclared is denied. Drop your
          agent's code to auto-detect its tools, classify each, and download a
          replayable <span style={{ color: C.text }}>axor.config.json</span> — the
          same file Replay and Regression evaluate against.
        </Coach>
        <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 4px" }}>Bring your agent. Leave governed.</h1>
        <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, marginBottom: 24 }}>
          Drop the code — we find its tools, you tell us what they can do, you download the wrapped package.
        </div>
        <div onClick={stage === "entry" ? upload : undefined} className="p-8 flex flex-col items-center gap-3"
          style={{ background: C.panel, border: `1px dashed ${stage === "analyzing" ? C.steel : C.line}`, borderRadius: 8, cursor: stage === "entry" ? "pointer" : "default" }}>
          {stage === "analyzing" ? (
            <><FileCode size={22} color={C.steel} />
              <span style={{ fontFamily: MONO, fontSize: 12, color: C.steel }}>analyzing my_agent/ — extracting tool signatures…</span>
              <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>names and signatures only — classes are yours to assign</span></>
          ) : (
            <><Upload size={22} color={C.mut} />
              <span style={{ fontFamily: MONO, fontSize: 12.5, color: C.text }}>Drop your agent folder or tools file</span>
              <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>.py · MCP manifest · LangChain project (click to simulate)</span></>
          )}
        </div>
        {stage === "entry" && (
          <>
            <Tooltip content="Skip detection: start from an empty sink list and add each tool by name yourself.">
              <button onClick={() => { setSinks([]); setStage("build"); }} className="mt-4" style={{ background: "none", border: "none", color: C.mut, fontFamily: MONO, fontSize: 11.5, cursor: "pointer", padding: 0 }}>
                declare sinks by hand instead
              </button>
            </Tooltip>
            <div className="flex items-start gap-2 mt-6 p-3" style={{ background: C.panel2, border: `1px solid ${C.line}`, borderRadius: 6 }}>
              <Terminal size={13} color={C.dim} style={{ marginTop: 1 }} />
              <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim, lineHeight: 1.6 }}>
                Code shouldn't leave your machine? <span style={{ color: C.mut }}>uvx axor wrap ./my_agent</span> — same screen, pre-filled, nothing uploaded.
              </div>
            </div>
          </>
        )}
      </Container>
    );
  }

  if (stage === "build") {
    return (
      <Container>
        <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 4px" }}>
          {fromCode ? <>Found {sinks.length} tools. What is each allowed to be?</> : <>What can your agent touch?</>}
        </h1>
        <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, marginBottom: 20 }}>
          {fromCode
            ? <>Detection reads names, never intent — <span style={{ color: C.amber }}>you assign the class</span>. Unclassified stays denied.</>
            : <>Declare its tools as sinks. Everything you don't declare will be denied.</>}
        </div>

        <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
          {sinks.map((s, i) => (
            <div key={s.name}>
              <div onClick={() => setSel(sel === i ? null : i)} className="flex items-center gap-3 px-4 py-3"
                style={{ cursor: "pointer", borderTop: i ? `1px solid ${C.line}` : "none" }}>
                {s.type === "?" ? (
                  <div className="flex gap-1" style={{ width: 168 }} onClick={(e) => e.stopPropagation()}>
                    {TYPES.map((t) => (
                      <button key={t} onClick={() => patch(i, { type: t })}
                        style={{ background: "none", border: `1px solid ${C.line}`, borderRadius: 3, color: typeColor(t), fontFamily: MONO, fontSize: 9, fontWeight: 700, padding: "3px 5px", cursor: "pointer" }}>{t}</button>
                    ))}
                  </div>
                ) : (
                  <span onClick={(e) => { e.stopPropagation(); patch(i, { type: "?" }); }}
                    style={{ fontFamily: MONO, fontSize: 10, fontWeight: 700, color: typeColor(s.type), width: 168 }}>{s.type}</span>
                )}
                <span style={{ fontFamily: MONO, fontSize: 13, color: s.type === "?" ? C.amber : C.text }}>{s.name}</span>
                {s.critical && <AlertTriangle size={12} color={C.red} />}
                <span style={{ flex: 1 }} />
                {s.args.some((a) => a.set.length) && <span style={{ fontFamily: MONO, fontSize: 10, color: C.green }}>{s.args.filter((a) => a.set.length).length} allowlist</span>}
                {s.src && <span style={{ fontFamily: MONO, fontSize: 10, color: C.dim }}>{s.src}</span>}
                <X size={13} color={C.dim} style={{ cursor: "pointer" }} onClick={(e) => { e.stopPropagation(); setSinks(sinks.filter((_, j) => j !== i)); setSel(null); }} />
              </div>
              {sel === i && (
                <div className="px-4 pb-4 flex flex-col gap-3" style={{ paddingLeft: 184 }}>
                  <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>{s.endpoint || "no endpoint"}</div>
                  {/* criticality */}
                  <div className="flex items-center gap-2">
                    <span style={{ fontFamily: MONO, fontSize: 10, color: C.dim, letterSpacing: "0.08em" }}>CRITICALITY</span>
                    {(["standard", "critical"] as const).map((c) => (
                      <button key={c} onClick={() => patch(i, { critical: c === "critical" })}
                        style={{ background: (s.critical ? "critical" : "standard") === c ? "rgba(127,168,204,0.1)" : "none", border: `1px solid ${(s.critical ? "critical" : "standard") === c ? (c === "critical" ? C.red : C.steel) : C.line}`, borderRadius: 4, color: (s.critical ? "critical" : "standard") === c ? (c === "critical" ? C.red : C.steel) : C.dim, fontFamily: MONO, fontSize: 10.5, padding: "4px 10px", cursor: "pointer" }}>
                        {c}
                      </button>
                    ))}
                    <span style={{ fontFamily: MONO, fontSize: 10, color: C.dim }}>
                      {s.critical ? "denials here escalate degradation immediately; evidence ranked first" : "amplifier only — standard never relaxes anything"}
                    </span>
                  </div>
                  {/* allowlists */}
                  <ArgEditor sink={s} update={(p) => patch(i, p)} />
                </div>
              )}
            </div>
          ))}
          {adding ? (
            <div className="flex items-center gap-2 px-4 py-3" style={{ borderTop: `1px solid ${C.line}` }}>
              <select value={draft.type} onChange={(e) => setDraft({ ...draft, type: e.target.value as ToolType })}
                style={{ background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: typeColor(draft.type), fontFamily: MONO, fontSize: 11, padding: "5px 6px" }}>
                {TYPES.map((t) => <option key={t}>{t}</option>)}
              </select>
              <input autoFocus placeholder="tool name ⏎" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && draft.name.trim()) {
                    setSinks([...sinks, { name: draft.name.trim(), type: draft.type, critical: false, args: [] }]);
                    setDraft({ name: "", type: "READ" });
                    setAdding(false);
                  }
                }}
                style={{ flex: 1, background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: C.text, fontFamily: MONO, fontSize: 12, padding: "6px 8px", outline: "none" }} />
            </div>
          ) : (
            <button onClick={() => setAdding(true)} className="flex items-center gap-2 px-4 py-3 w-full"
              style={{ background: "none", border: "none", borderTop: sinks.length ? `1px solid ${C.line}` : "none", color: C.mut, fontFamily: MONO, fontSize: 12, cursor: "pointer" }}>
              <Plus size={13} /> Add tool
            </button>
          )}
        </div>

        <div className="mt-2">
          <Fold label="budgets (optional)">
            <div className="flex items-center gap-2 flex-wrap">
              <span style={{ fontFamily: MONO, fontSize: 11.5, color: C.text }}>max tool calls per run</span>
              <input type="number" min={1} value={budgetCapCalls ?? ""} placeholder="unlimited"
                onChange={(e) => {
                  const n = parseInt(e.target.value, 10);
                  setBudgetCapCalls(e.target.value === "" || Number.isNaN(n) || n < 1 ? null : n);
                }}
                style={{ width: 100, background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: C.text, fontFamily: MONO, fontSize: 11.5, padding: "5px 8px", outline: "none" }} />
              <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>
                {budgetCapCalls !== null
                  ? "enforced at the loop boundary — exhaustion is a typed denial, never a silent overrun"
                  : "empty = unlimited — budgets are opt-in limits, not fail-closed defaults"}
              </span>
            </div>
          </Fold>
          <Fold label="inter-federation peers (A2A, optional)">
            <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginBottom: 8 }}>
              declared like sinks: identity (pubkey) · trust level · message classes.
              undeclared = L0 (full taint in, untrusted export destination out).
              declaration buys discount, never label authority.
            </div>
            {peers.map((p, i) => (
              <div key={p.peer_id} className="flex items-center gap-3 py-1.5" style={{ fontFamily: MONO, fontSize: 11.5 }}>
                <span style={{ color: C.text }}>{p.peer_id}</span>
                <span style={{ color: p.level === "L2" ? "#9B8CCC" : p.level === "L1" ? C.steel : C.dim, fontWeight: 700, fontSize: 10 }}>
                  {p.level}{p.attested ? " · attested" : ""}
                </span>
                <span style={{ color: C.dim, fontSize: 10 }}>{p.classes || "no discount classes"}</span>
                <button onClick={() => setPeers(peers.filter((_, j) => j !== i))}
                  style={{ background: "none", border: "none", color: C.dim, cursor: "pointer", fontFamily: MONO, fontSize: 10 }}>remove</button>
              </div>
            ))}
            {peerDraft ? (
              <div className="flex items-center gap-2 flex-wrap py-1.5">
                <input placeholder="peer id" value={peerDraft.peer_id}
                  onChange={(e) => setPeerDraft({ ...peerDraft, peer_id: e.target.value })}
                  style={{ width: 110, background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: C.text, fontFamily: MONO, fontSize: 11, padding: "5px 7px", outline: "none" }} />
                <input placeholder="ed25519 pubkey (hex)" value={peerDraft.pubkey}
                  onChange={(e) => setPeerDraft({ ...peerDraft, pubkey: e.target.value })}
                  style={{ width: 180, background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: C.text, fontFamily: MONO, fontSize: 11, padding: "5px 7px", outline: "none" }} />
                {(["L0", "L1", "L2"] as PeerLevel[]).map((l) => (
                  <button key={l} onClick={() => setPeerDraft({ ...peerDraft, level: l })}
                    style={{ background: peerDraft.level === l ? "rgba(127,168,204,0.15)" : "none", border: `1px solid ${C.line}`, borderRadius: 3, color: peerDraft.level === l ? C.text : C.dim, fontFamily: MONO, fontSize: 10, fontWeight: 700, padding: "4px 7px", cursor: "pointer" }}>{l}</button>
                ))}
                {peerDraft.level === "L2" && (
                  <>
                    <input placeholder="message classes (comma)" value={peerDraft.classes}
                      onChange={(e) => setPeerDraft({ ...peerDraft, classes: e.target.value })}
                      style={{ width: 170, background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: C.text, fontFamily: MONO, fontSize: 11, padding: "5px 7px", outline: "none" }} />
                    <label className="flex items-center gap-1" style={{ fontFamily: MONO, fontSize: 10, color: C.mut, cursor: "pointer" }}>
                      <input type="checkbox" checked={peerDraft.attested}
                        onChange={(e) => setPeerDraft({ ...peerDraft, attested: e.target.checked })} />
                      governance-attested
                    </label>
                  </>
                )}
                <button
                  onClick={() => {
                    if (!peerDraft.peer_id || !peerDraft.pubkey) return;
                    setPeers([...peers, peerDraft]);
                    setPeerDraft(null);
                  }}
                  disabled={!peerDraft.peer_id || !peerDraft.pubkey}
                  style={btn({ color: (!peerDraft.peer_id || !peerDraft.pubkey) ? C.dim : C.steel, fontSize: 10.5, padding: "4px 10px" })}>
                  declare
                </button>
              </div>
            ) : (
              <button onClick={() => setPeerDraft({ peer_id: "", pubkey: "", level: "L1", classes: "", attested: false })}
                style={{ background: "none", border: "none", color: C.mut, fontFamily: MONO, fontSize: 11.5, cursor: "pointer", display: "flex", alignItems: "center", gap: 6, padding: "4px 0" }}>
                <Plus size={12} /> Declare a peer
              </button>
            )}
          </Fold>
        </div>

        <div className="mt-4 flex items-center gap-3">
          <Tooltip content={unclassified > 0
            ? "Assign a consequence class to every tool first — unclassified tools stay denied, so the config isn't ready."
            : "See the config in plain words (what's allowed, what's denied, what's capped) before you download it."}>
            <button onClick={() => unclassified === 0 && sinks.length > 0 && setStage("preview")} disabled={unclassified > 0 || sinks.length === 0}
              style={btn({ color: unclassified || !sinks.length ? C.dim : C.text, borderColor: unclassified || !sinks.length ? C.line : C.steel, padding: "9px 18px", fontSize: 12.5, opacity: unclassified || !sinks.length ? 0.6 : 1, cursor: unclassified || !sinks.length ? "default" : "pointer" })}>
              Preview config <ArrowRight size={13} />
            </button>
          </Tooltip>
          {unclassified > 0 && <span style={{ fontFamily: MONO, fontSize: 11.5, color: C.amber }}>{unclassified} unclassified — still denied</span>}
        </div>
      </Container>
    );
  }

  // preview
  const readings: React.ReactNode[] = [
    ...sinks.filter((s) => s.critical).map((s) => <span key={`crit-${s.name}`}><b style={{ color: C.red }}>{s.name}</b> is critical — a denial there escalates degradation immediately and its evidence is shown first.</span>),
    ...sinks.filter((s) => s.type === "EXPORT").map((s) => <span key={`exp-${s.name}`}>Exports through <b style={{ color: C.text }}>{s.name}</b> require untainted values{s.args.some((a) => a.set.length) ? <> or membership in {s.args.filter((a) => a.set.length).map((a) => a.arg).join(", ")}</> : null}.</span>),
    ...sinks.filter((s) => s.type === "EXEC").map((s) => <span key={`exec-${s.name}`}><b style={{ color: C.text }}>{s.name}</b> after an external read is denied.</span>),
    ...peers.map((p) => <span key={`peer-${p.peer_id}`}>Peer <b style={{ color: C.text }}>{p.peer_id}</b> is {p.level}{p.attested ? " (governance-attested)" : ""} — {p.level === "L2" ? `signed assertions get a bounded discount on ${p.classes || "no"} classes; critical sinks ignore it` : p.level === "L1" ? "identity verified, inbound taint unchanged (attribution, not trust)" : "full taint inbound, untrusted export destination outbound"}. Any peer NOT declared here is L0.</span>),
    ...sinks.filter((s) => s.type === "WRITE").map((s) => <span key={`write-${s.name}`}><b style={{ color: C.text }}>{s.name}</b> writes are gated on value provenance.</span>),
    budgetCapCalls !== null
      ? <span key="budget">Budget: at most <b style={{ color: C.text }}>{budgetCapCalls}</b> tool calls per run — the cap is enforced at the loop boundary, and the same ceiling the replay kernel checks, so a run and its counterfactual agree on exhaustion.</span>
      : <span key="budget">No budget declared — unlimited (budgets are opt-in limits, unlike sinks).</span>,
  ];

  return (
    <Container>
      <button onClick={() => { setStage("build"); setEmitted(false); }} style={{ background: "none", border: "none", color: C.dim, fontFamily: MONO, fontSize: 11.5, cursor: "pointer", padding: 0, marginBottom: 12 }}>← back to sinks</button>
      <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 20px" }}>Here's what this config means.</h1>
      <div className="p-4 flex flex-col gap-2" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
        {readings.map((r, i) => <div key={i} style={{ fontFamily: MONO, fontSize: 12.5, color: C.mut, lineHeight: 1.6 }}>{r}</div>)}
        <div className="mt-1 pt-3 flex items-center gap-2" style={{ borderTop: `1px solid ${C.line}` }}>
          <span style={{ fontFamily: MONO, fontSize: 12.5, color: C.red, fontWeight: 700 }}>Anything not listed above is denied.</span>
          <span style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>— fail-closed; you are confirming this, not discovering it later</span>
        </div>
      </div>
      {fromCode && (
        <Fold label="wrapped package — your code untouched, two files added" openDefault>
          <div style={{ background: C.panel2, border: `1px solid ${C.line}`, borderRadius: 6, padding: 12, fontFamily: MONO, fontSize: 11.5, lineHeight: 1.8 }}>
            <div style={{ color: C.dim }}>my_agent/</div>
            <div style={{ color: C.dim, paddingLeft: 16 }}>agent.py · tools.py · db.py <span style={{ fontSize: 10 }}>— unchanged</span></div>
            <div style={{ color: C.green, paddingLeft: 16 }}>+ axor_wrapper.py</div>
            <div style={{ color: C.green, paddingLeft: 16 }}>+ axor.config.json</div>
          </div>
        </Fold>
      )}
      <Fold label="generated config (axor.config.json)">
        <pre style={{ background: C.panel2, border: `1px solid ${C.line}`, borderRadius: 6, padding: 12, fontFamily: MONO, fontSize: 11, color: C.mut, overflow: "auto", margin: 0 }}>{JSON.stringify(config, null, 2)}</pre>
      </Fold>
      <div className="flex items-center gap-3 mt-5">
        <Tooltip content="Downloads axor.config.json — drop it next to your agent and run it governed. The same file drives Replay counterfactuals and Regression.">
          <button onClick={download} style={btn({ color: C.text, borderColor: C.steel, padding: "9px 18px", fontSize: 12.5 })}>
            <Download size={14} /> {fromCode ? "Download wrapped package" : "Download config + scaffold"}
          </button>
        </Tooltip>
        {emitted && (
          <span style={{ fontFamily: MONO, fontSize: 12, color: C.green, display: "flex", alignItems: "center", gap: 6 }}>
            <Check size={13} /> saved · <span style={{ color: C.steel, cursor: "pointer" }}>run first governed experiment →</span>
          </span>
        )}
      </div>
    </Container>
  );
}
