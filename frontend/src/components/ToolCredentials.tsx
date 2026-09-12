// The operator surface for the tool-credential vault (ui-spec §14.2).
//
// Everything this pane does was reachable only by curl: enrol, rotate, revoke,
// turn envelope mode on, register the keys that make a dispense verifiable, and
// read what every dispensed credential was fetched for. A mechanism with no
// operator surface is a mechanism nobody runs.
//
// Where the sealing happens is the one real decision here. In envelope mode the
// secret is sealed IN THIS BROWSER, on the operator's own machine, and only the
// ciphertext is posted — one step for them, and one fewer place the plaintext
// exists than a shell command would leave it (no history, no scrollback, no
// file). The wasm is libsodium's own sealed box, loaded lazily and only when
// someone enrols; nothing here reimplements a primitive.
//
// The private halves are never typed here. `axor-proxy vault keygen` mints them
// on the machine that will hold them, and only public halves reach this form —
// pasting a private key into a browser would hand over the one thing the mode
// exists to keep away from the backend.
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Lock, LockOpen, Plus, RotateCw, Slash } from "lucide-react";
import { api, VaultCredential } from "../api";
import { useApp } from "../store";
import { C, MONO, btn } from "../theme";
import Tooltip from "./Tooltip";

const input = {
  background: C.bg, border: `1px solid ${C.line}`, borderRadius: 5,
  color: C.text, fontFamily: MONO, fontSize: 10.5, padding: "4px 7px",
  width: "100%",
} as const;

const label = { fontFamily: MONO, fontSize: 9.5, color: C.dim, marginBottom: 2 } as const;

function Field({ name, value, onChange, placeholder, type }: {
  name: string; value: string; placeholder?: string; type?: string;
  onChange: (v: string) => void;
}) {
  return (
    <div style={{ flex: 1, minWidth: 110 }}>
      <div style={label}>{name}</div>
      <input
        style={input} value={value} placeholder={placeholder} type={type}
        aria-label={name}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}

export default function ToolCredentials() {
  const qc = useQueryClient();
  const credsToken = useApp((s) => s.vaultCredsToken);
  const setCredsToken = useApp((s) => s.setVaultCredsToken);

  const health = useQuery({ queryKey: ["vault-creds"], queryFn: api.vaultCredsHealth });
  const sealing = useQuery({ queryKey: ["vault-sealing"], queryFn: api.vaultSealingKey });
  const audit = useQuery({ queryKey: ["vault-creds-audit"], queryFn: () => api.vaultCredsAudit() });
  // Which nodes sign their dispenses. Registering one was possible and seeing
  // what was registered was not, which makes "is this node signing?" a question
  // the panel could not answer about the deployment it is showing.
  const nodeKeys = useQuery({ queryKey: ["vault-node-keys"], queryFn: api.vaultNodeKeys });

  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState({
    tool: "", endpoint: "", secret: "", scope: "",
    header: "Authorization", scheme: "Bearer",
  });
  const [err, setErr] = useState<string | null>(null);
  const [sealKey, setSealKey] = useState("");
  const [nodeKey, setNodeKey] = useState({ node: "", pub: "" });

  const sealingKey = sealing.data?.public_key_hex ?? null;
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["vault-creds"] });
    void qc.invalidateQueries({ queryKey: ["vault-creds-audit"] });
  };
  const fail = (e: Error) => setErr(e.message);

  const enroll = useMutation({
    mutationFn: () =>
      api.vaultEnroll({
        tool: draft.tool.trim(), endpoint: draft.endpoint.trim(),
        secret: draft.secret,
        scope_nodes: draft.scope.split(",").map((n) => n.trim()).filter(Boolean),
        header: draft.header.trim() || "Authorization", scheme: draft.scheme,
      }, sealingKey),
    onSuccess: () => {
      setErr(null);
      setDraft({ ...draft, tool: "", endpoint: "", secret: "", scope: "" });
      setOpen(false);
      refresh();
    },
    onError: fail,
  });

  const rotate = useMutation({
    mutationFn: ({ e, secret }: { e: VaultCredential; secret: string }) =>
      api.vaultRotate(e.tool, e.endpoint, secret, sealingKey),
    onSuccess: () => { setErr(null); refresh(); },
    onError: fail,
  });

  const revoke = useMutation({
    mutationFn: (e: VaultCredential) => api.vaultRevoke(e.tool, e.endpoint),
    onSuccess: () => { setErr(null); refresh(); },
    onError: fail,
  });

  const registerSealing = useMutation({
    mutationFn: () => api.registerSealingKey(sealKey.trim()),
    onSuccess: () => {
      setErr(null);
      setSealKey("");
      void qc.invalidateQueries({ queryKey: ["vault-sealing"] });
    },
    onError: fail,
  });

  const registerNode = useMutation({
    mutationFn: () => api.registerNodeKey(nodeKey.node.trim(), nodeKey.pub.trim()),
    onSuccess: () => {
      setErr(null);
      setNodeKey({ node: "", pub: "" });
      void qc.invalidateQueries({ queryKey: ["vault-node-keys"] });
    },
    onError: fail,
  });

  const enrolled = health.data?.enrolled ?? [];
  const plaintext = enrolled.filter((e) => !e.sealed).length;

  return (
    <div className="p-4 flex-1" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }} data-testid="tool-credentials">
      <div style={{ fontSize: 10, fontFamily: MONO, color: C.dim, letterSpacing: "0.1em", marginBottom: 8 }}>
        FEDERATION VAULT · TOOL CREDENTIALS
      </div>
      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, marginBottom: 10, lineHeight: 1.6 }}>
        fetched at call time and injected at the sink by the proxy, for the tools
        its AXOR_VAULT_TOOLS opts in — so the agent never holds the key. per-node
        scope, fail-closed, no cache. rotation is config; the plane may revoke,
        never grant.
      </div>

      {/* Whether this deployment can read what it stores. Silence here is what
          would let plaintext custody look like envelope mode. */}
      {sealing.data && (
        <div
          className="flex items-center gap-2 mb-3 p-2"
          style={{
            fontFamily: MONO, fontSize: 10, borderRadius: 5,
            border: `1px solid ${sealing.data.envelope_mode ? C.line : C.amber}`,
            color: sealing.data.envelope_mode ? C.green : C.amber,
          }}
        >
          {sealing.data.envelope_mode ? <Lock size={12} /> : <LockOpen size={12} />}
          {sealing.data.envelope_mode ? (
            <span>
              envelope mode · this deployment stores only what it cannot open
              {plaintext > 0 && (
                <span style={{ color: C.amber }}>
                  {" "}· {plaintext} enrolled before the switch still held in plaintext — rotate them
                </span>
              )}
            </span>
          ) : (
            <span>
              this deployment stores credentials in PLAINTEXT. Mint a sealing key
              with <span style={{ color: C.text }}>axor-proxy vault keygen</span> and
              register the public half below.
            </span>
          )}
        </div>
      )}

      <div style={{ marginBottom: 8 }}>
        <div style={label}>vault creds token (X-Vault-Creds-Token)</div>
        <input
          style={input} type="password" value={credsToken} aria-label="creds token"
          placeholder="required when the deployment sets AXOR_VAULT_CREDS_TOKEN"
          onChange={(e) => setCredsToken(e.target.value)}
        />
      </div>

      {enrolled.length === 0 ? (
        <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>no credentials enrolled</div>
      ) : (
        enrolled.map((e) => (
          <div key={`${e.tool}-${e.endpoint}`} className="flex items-center gap-2 py-1" style={{ fontFamily: MONO, fontSize: 11 }}>
            <span style={{ color: e.revoked ? C.dim : C.text, textDecoration: e.revoked ? "line-through" : "none" }}>{e.tool}</span>
            <span style={{ color: C.dim, fontSize: 10 }}>v{e.version}</span>
            <span style={{ color: C.dim, fontSize: 10 }}>scope: {e.scope_nodes.join(", ") || "none"}</span>
            <span style={{ color: C.dim, fontSize: 10 }}>→ {e.header}{e.scheme ? ` ${e.scheme}` : ""}</span>
            <Tooltip content={e.sealed
              ? "Sealed: the plane holds a ciphertext it has no key for."
              : "Plaintext: this deployment can read this credential."}>
              <span style={{ color: e.sealed ? C.green : C.amber, fontSize: 10 }}>
                {e.sealed ? "sealed" : "plaintext"}
              </span>
            </Tooltip>
            {e.revoked && <span style={{ color: C.red, fontSize: 10 }}>revoked</span>}
            <span style={{ flex: 1 }} />
            <Tooltip content="New secret, same scope. A revoked credential refuses rotation — granting it again is enrolment, deliberately.">
              <button
                aria-label={`rotate ${e.tool}`}
                onClick={() => {
                  const secret = window.prompt(`New secret for ${e.tool} — ${e.endpoint}:`);
                  if (secret) rotate.mutate({ e, secret });
                }}
                style={btn({ color: C.mut, fontSize: 10, padding: "2px 7px" })}
              >
                <RotateCw size={11} /> rotate
              </button>
            </Tooltip>
            <Tooltip content="Narrowing, available during an incident. The very next call is denied — there is no cache to expire.">
              <button
                aria-label={`revoke ${e.tool}`}
                disabled={e.revoked}
                onClick={() => revoke.mutate(e)}
                style={btn({ color: e.revoked ? C.dim : C.red, fontSize: 10, padding: "2px 7px" })}
              >
                <Slash size={11} /> revoke
              </button>
            </Tooltip>
          </div>
        ))
      )}

      <button
        onClick={() => setOpen(!open)}
        style={btn({ color: C.steel, fontSize: 10.5, padding: "3px 9px", marginTop: 8 })}
      >
        <Plus size={11} /> enrol a credential
      </button>

      {open && (
        <div className="mt-2 p-2" style={{ border: `1px solid ${C.line}`, borderRadius: 6 }}>
          <div className="flex gap-2 mb-2">
            <Field name="tool" value={draft.tool} onChange={(v) => setDraft({ ...draft, tool: v })} />
            <Field name="endpoint" value={draft.endpoint} placeholder="https://api.stripe.com" onChange={(v) => setDraft({ ...draft, endpoint: v })} />
          </div>
          <div className="flex gap-2 mb-2">
            <Field name="scope_nodes (comma-separated)" value={draft.scope} onChange={(v) => setDraft({ ...draft, scope: v })} />
          </div>
          <div className="flex gap-2 mb-2">
            <Field name="header" value={draft.header} onChange={(v) => setDraft({ ...draft, header: v })} />
            <Field name="scheme" value={draft.scheme} placeholder="empty for a raw API key" onChange={(v) => setDraft({ ...draft, scheme: v })} />
          </div>
          <Field name="secret" type="password" value={draft.secret} onChange={(v) => setDraft({ ...draft, secret: v })} />
          <div style={{ fontFamily: MONO, fontSize: 9.5, color: sealingKey ? C.green : C.amber, margin: "6px 0" }}>
            {sealingKey
              ? "sealed in this browser before it is sent — the plaintext never leaves this machine"
              : "sent as plaintext: no sealing key is registered, so this deployment will store it as-is"}
          </div>
          <button
            onClick={() => enroll.mutate()}
            disabled={enroll.isPending || !draft.tool || !draft.endpoint || !draft.secret}
            style={btn({ color: C.bg, background: C.steel, border: `1px solid ${C.steel}`, fontSize: 10.5, padding: "4px 10px" })}
          >
            {enroll.isPending ? "enrolling…" : "enrol"}
          </button>
        </div>
      )}

      {/* Public halves only. Both private halves are minted by `axor-proxy vault
          keygen` on the machine that will hold them. */}
      <div className="mt-3 pt-2" style={{ borderTop: `1px solid ${C.line}` }}>
        <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, letterSpacing: "0.08em", marginBottom: 4 }}>
          PUBLIC KEYS · private halves stay on the machines that hold them
        </div>
        <div className="flex gap-2 items-end mb-2">
          <Field name="sealing pubkey (envelope mode)" value={sealKey} onChange={setSealKey} />
          <button
            onClick={() => registerSealing.mutate()}
            disabled={!sealKey.trim() || registerSealing.isPending}
            style={btn({ color: C.mut, fontSize: 10, padding: "4px 9px" })}
          >
            register
          </button>
        </div>
        <div className="flex gap-2 items-end">
          <Field name="node id" value={nodeKey.node} onChange={(v) => setNodeKey({ ...nodeKey, node: v })} />
          <Field name="node pubkey (signs its dispenses)" value={nodeKey.pub} onChange={(v) => setNodeKey({ ...nodeKey, pub: v })} />
          <button
            onClick={() => registerNode.mutate()}
            disabled={!nodeKey.node.trim() || !nodeKey.pub.trim() || registerNode.isPending}
            style={btn({ color: C.mut, fontSize: 10, padding: "4px 9px" })}
          >
            register
          </button>
        </div>
        {Object.keys(nodeKeys.data ?? {}).length > 0 && (
          <div className="mt-2">
            {Object.entries(nodeKeys.data ?? {}).map(([node, pub]) => (
              <div key={node} className="flex items-center gap-2" style={{ fontFamily: MONO, fontSize: 10, color: C.mut }}>
                <span style={{ color: C.text }}>{node}</span>
                <span style={{ color: C.dim }}>{pub.slice(0, 16)}…</span>
                <Tooltip content="This node signs its dispense attestations. An unsigned fetch from it is refused — registering a key is the deployment saying so.">
                  <span style={{ color: C.green }}>signs</span>
                </Tooltip>
              </div>
            ))}
          </div>
        )}
      </div>

      {(audit.data ?? []).length > 0 && (
        <div className="mt-3 pt-2" style={{ borderTop: `1px solid ${C.line}` }}>
          <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, letterSpacing: "0.08em", marginBottom: 4 }}>
            DISPENSE LOG · what each credential was fetched for
          </div>
          {/* Newest first, straight from the route — see Settings.tsx. */}
          {(audit.data ?? []).map((row, i) => (
            <div key={i} className="flex items-center gap-2 py-0.5" style={{ fontFamily: MONO, fontSize: 10, color: C.mut }}>
              <span style={{ color: row.signed ? C.green : C.dim }}>{row.signed ? "signed" : "unsigned"}</span>
              <span style={{ color: C.text }}>{row.tool}</span>
              <span>{row.node_id}</span>
              {row.run_id && <span style={{ color: C.dim }}>{row.run_id}</span>}
              <span style={{ color: C.dim }}>{row.verdict ?? "no verdict"}</span>
            </div>
          ))}
        </div>
      )}

      {err && (
        <div className="mt-2" style={{ fontFamily: MONO, fontSize: 10, color: C.red }}>{err}</div>
      )}
    </div>
  );
}
