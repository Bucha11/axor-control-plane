// Settings — the "loud elsewhere" companion to quiet-until-wrong (spec section
// 16) plus connection and license. Notifications emit-and-route (webhook), with
// the dead-letter log surfaced honestly; a notification system that fails
// silently is worse than none.
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, Loader2, Trash2 } from "lucide-react";
import { api } from "../api";
import { MODE_LABEL, useApp } from "../store";
import { C, MONO, btn } from "../theme";
import Coach from "../components/Coach";
import { useStartTour } from "../components/Tour";

const TRIGGERS = [
  { id: "level_transition_up", label: "degradation level rises" },
  { id: "evidence_run", label: "run completes with an EvidenceCase" },
  { id: "heat_threshold", label: "Sentinel heat crosses a threshold" },
  { id: "node_stale", label: "a node goes stale" },
  { id: "regression_failed", label: "a corpus run regresses (config CI)" },
];

const KEY_SCOPES = ["read", "ingest", "operate", "admin"];

export default function Settings() {
  const { mode, testBench } = useApp((s) => s.connection);
  const setTestBench = useApp((s) => s.setTestBench);
  const disconnect = useApp((s) => s.disconnect);
  const apiToken = useApp((s) => s.apiToken);
  const setApiToken = useApp((s) => s.setApiToken);
  const qc = useQueryClient();

  const [url, setUrl] = useState("");
  const [selected, setSelected] = useState<string[]>(["level_transition_up", "evidence_run"]);
  const [chanLabel, setChanLabel] = useState("");
  const [nodePattern, setNodePattern] = useState("");
  const [licenseJson, setLicenseJson] = useState("");
  const [vendorKey, setVendorKey] = useState("");
  const [keyScopes, setKeyScopes] = useState<string[]>(["ingest"]);
  const [keyLabel, setKeyLabel] = useState("");
  const [mintedSecret, setMintedSecret] = useState<string | null>(null);

  const deadLetters = useQuery({ queryKey: ["dead-letters"], queryFn: api.deadLetters });
  const authStatus = useQuery({ queryKey: ["auth-status", apiToken], queryFn: api.authStatus });
  const keys = useQuery({
    queryKey: ["api-keys"],
    queryFn: api.listKeys,
    enabled: authStatus.data?.authenticated === true && (authStatus.data?.scopes ?? []).includes("admin"),
    retry: false,
  });

  const mintKey = useMutation({
    mutationFn: () => api.createKey(keyScopes, keyLabel),
    onSuccess: (r) => {
      setMintedSecret(r.secret);
      void qc.invalidateQueries({ queryKey: ["api-keys"] });
    },
  });
  const revokeKey = useMutation({
    mutationFn: (keyId: string) => api.revokeKey(keyId),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["api-keys"] }),
  });

  const licenseStatus = useQuery({ queryKey: ["license-status"], queryFn: api.licenseStatus });
  const eeActive = licenseStatus.data?.active === true;
  const subscriptions = useQuery({ queryKey: ["subscriptions"], queryFn: api.listSubscriptions });

  const subscribe = useMutation({
    mutationFn: () =>
      api.subscribeNotifications(url, selected, 0, {
        label: chanLabel || undefined,
        nodePattern: nodePattern || undefined,
      }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["subscriptions"] }),
  });
  const license = useMutation({
    mutationFn: () => api.verifyLicense(licenseJson, vendorKey),
  });

  const toggle = (id: string) =>
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));

  const learnMode = useApp((s) => s.learnMode);
  const setLearnMode = useApp((s) => s.setLearnMode);
  const coachDismissed = useApp((s) => s.coachDismissed);
  const resetCoach = useApp((s) => s.resetCoach);
  const startTour = useStartTour();

  return (
    <div style={{ maxWidth: 640, margin: "0 auto" }}>
      <Coach id="settings" title="Settings — auth, alerts, license">
        Auth is opt-in (empty token = open dev backend). Notifications are the
        loud half of quiet-until-wrong: the UI stays calm, the webhook fires when
        something is actually wrong — and failed deliveries land in the
        dead-letter log instead of vanishing.
      </Coach>
      <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 20px" }}>Settings</h1>

      {/* Learn mode — the adoption layer's own controls */}
      <Section title="LEARN MODE">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <span style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut }}>
            coach notes on each screen — off by default, this stays quiet-until-wrong
          </span>
          <div className="flex items-center gap-2">
            <button
              onClick={startTour}
              title="a 7-stop spotlight walkthrough of the whole funnel"
              style={btn({ color: C.steel, borderColor: C.steel, fontSize: 11, padding: "4px 12px" })}
            >
              start tour
            </button>
            <button
              onClick={() => setLearnMode(!learnMode)}
              style={btn({ color: learnMode ? C.steel : C.mut, borderColor: learnMode ? C.steel : C.line, fontSize: 11, padding: "4px 12px" })}
            >
              {learnMode ? "on" : "off"}
            </button>
            <button
              onClick={resetCoach}
              disabled={coachDismissed.length === 0}
              title="bring back every dismissed coach note"
              style={btn({ color: coachDismissed.length ? C.mut : C.dim, fontSize: 11, padding: "4px 12px" })}
            >
              reset tips{coachDismissed.length ? ` (${coachDismissed.length})` : ""}
            </button>
          </div>
        </div>
      </Section>

      {/* Authentication (architecture section 9) */}
      <Section title="AUTHENTICATION">
        {authStatus.data && !authStatus.data.auth_enabled ? (
          <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut }}>
            Auth is off — this backend is open (dev / self-hosted without a token).
            Set <span style={{ color: C.text }}>AXOR_API_TOKEN</span> on the backend to
            require a token here.
          </div>
        ) : (
          <>
            <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginBottom: 8 }}>
              local token or an API key — sent as the bearer on every request
              (SSE + export carry it as a query param).
            </div>
            <div className="flex items-center gap-2 mb-2">
              <input
                type="password"
                value={apiToken}
                onChange={(e) => setApiToken(e.target.value)}
                placeholder="backend token"
                className="w-full"
                style={{ background: C.bg, border: `1px solid ${C.line}`, borderRadius: 5, color: C.text, fontFamily: MONO, fontSize: 12, padding: "7px 9px", outline: "none" }}
              />
              <span style={{ fontFamily: MONO, fontSize: 11, whiteSpace: "nowrap",
                color: authStatus.data?.authenticated ? C.green : C.red }}>
                {authStatus.data?.authenticated ? "● authenticated" : "○ locked"}
              </span>
            </div>
            {authStatus.data?.authenticated && (
              <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>
                scopes: {(authStatus.data.scopes ?? []).join(", ") || "none"}
              </div>
            )}

            {/* API key management (admin) */}
            {(authStatus.data?.scopes ?? []).includes("admin") && (
              <div className="mt-4">
                <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, letterSpacing: "0.08em", marginBottom: 8 }}>
                  API KEYS · scoped connections (e.g. an ingest key for the proxy)
                </div>
                <div className="flex items-center gap-2 flex-wrap mb-2">
                  {KEY_SCOPES.map((s) => (
                    <button key={s}
                      onClick={() => setKeyScopes((cur) => cur.includes(s) ? cur.filter((x) => x !== s) : [...cur, s])}
                      style={btn({ color: keyScopes.includes(s) ? C.steel : C.dim, borderColor: keyScopes.includes(s) ? C.steel : C.line, fontSize: 10.5, padding: "4px 10px" })}>
                      {s}
                    </button>
                  ))}
                  <input value={keyLabel} onChange={(e) => setKeyLabel(e.target.value)} placeholder="label"
                    style={{ background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: C.text, fontFamily: MONO, fontSize: 11, padding: "4px 8px", width: 120, outline: "none" }} />
                  <button onClick={() => mintKey.mutate()} disabled={keyScopes.length === 0 || mintKey.isPending}
                    style={btn({ color: C.steel, fontSize: 11 })}>
                    {mintKey.isPending ? <Loader2 size={12} className="animate-spin" /> : null} Mint key
                  </button>
                </div>
                {mintedSecret && (
                  <div className="flex items-center gap-2 mb-2 p-2" style={{ background: C.panel2, border: `1px solid ${C.line}`, borderRadius: 5 }}>
                    <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.green, wordBreak: "break-all", flex: 1 }}>{mintedSecret}</span>
                    <Copy size={13} color={C.mut} style={{ cursor: "pointer" }}
                      onClick={() => void navigator.clipboard?.writeText(mintedSecret)} />
                    <span style={{ fontFamily: MONO, fontSize: 10, color: C.dim }}>shown once</span>
                  </div>
                )}
                {(keys.data ?? []).map((k) => (
                  <div key={k.key_id} className="flex items-center gap-2 py-1">
                    <span style={{ fontFamily: MONO, fontSize: 11, color: C.text }}>{k.key_id}</span>
                    <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut }}>{k.scopes.join(",")}</span>
                    <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, flex: 1 }}>{k.label}</span>
                    <Trash2 size={12} color={C.dim} style={{ cursor: "pointer" }} onClick={() => revokeKey.mutate(k.key_id)} />
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </Section>

      {/* Connection */}
      <Section title="CONNECTION">
        <div style={{ fontFamily: MONO, fontSize: 12, color: C.text, marginBottom: 8 }}>
          {MODE_LABEL[mode]}
        </div>
        <label className="flex items-center gap-2" style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, cursor: "pointer" }}>
          <input type="checkbox" checked={testBench} onChange={(e) => setTestBench(e.target.checked)} />
          test-bench connection — enables injection & self-heal (spec decision 5)
        </label>
        {mode !== "none" && (
          <button onClick={disconnect} className="mt-3" style={btn({ color: C.mut, fontSize: 11 })}>
            disconnect
          </button>
        )}
      </Section>

      {/* Notifications */}
      <Section title="NOTIFICATIONS · WEBHOOK">
        <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginBottom: 10 }}>
          the UI is quiet; the notification channel is where wrong gets loud. We emit
          and route — Slack/Discord/PagerDuty are webhook consumers, not a pager.
        </div>
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://hooks.example/… (JSON POST)"
          className="w-full mb-3"
          style={{ background: C.bg, border: `1px solid ${C.line}`, borderRadius: 5, color: C.text, fontFamily: MONO, fontSize: 12, padding: "7px 9px", outline: "none" }}
        />
        <div className="flex flex-col gap-2 mb-3">
          {TRIGGERS.map((t) => (
            <label key={t.id} className="flex items-center gap-2" style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, cursor: "pointer" }}>
              <input type="checkbox" checked={selected.includes(t.id)} onChange={() => toggle(t.id)} />
              {t.label}
            </label>
          ))}
        </div>
        {/* Routing (EE): named channel + node glob — the org layer. Inputs
            stay visible but disabled without a license (honest upsell). */}
        <div className="flex items-center gap-2 mb-3" style={{ opacity: eeActive ? 1 : 0.55 }}>
          <input
            value={chanLabel}
            onChange={(e) => setChanLabel(e.target.value)}
            placeholder="channel label (e.g. team-a)"
            disabled={!eeActive}
            style={{ background: C.bg, border: `1px solid ${C.line}`, borderRadius: 5, color: C.text, fontFamily: MONO, fontSize: 11, padding: "5px 8px", width: 170, outline: "none" }}
          />
          <input
            value={nodePattern}
            onChange={(e) => setNodePattern(e.target.value)}
            placeholder="node pattern (e.g. team-a-*)"
            disabled={!eeActive}
            style={{ background: C.bg, border: `1px solid ${C.line}`, borderRadius: 5, color: C.text, fontFamily: MONO, fontSize: 11, padding: "5px 8px", width: 170, outline: "none" }}
          />
          <span style={{ fontFamily: MONO, fontSize: 8.5, color: C.dim, border: `1px solid ${C.line}`, borderRadius: 20, padding: "1px 6px" }}>
            Team
          </span>
          {!eeActive && (
            <span style={{ fontFamily: MONO, fontSize: 10, color: C.dim }}>
              routing needs a license — one global webhook is free
            </span>
          )}
        </div>
        <button
          onClick={() => subscribe.mutate()}
          disabled={!url || selected.length === 0 || subscribe.isPending}
          style={btn({ color: C.steel, fontSize: 12 })}
        >
          {subscribe.isPending ? <Loader2 size={13} className="animate-spin" /> : subscribe.isSuccess ? <Check size={13} /> : null}
          {subscribe.isSuccess ? "subscribed" : "Subscribe"}
        </button>
        {subscribe.isError && (
          <div className="mt-2" style={{ fontFamily: MONO, fontSize: 11, color: C.red }}>
            {(subscribe.error as Error).message}
          </div>
        )}

        {(subscriptions.data ?? []).length > 0 && (
          <>
            <div className="mt-4" style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, letterSpacing: "0.08em" }}>
              ACTIVE SUBSCRIPTIONS
            </div>
            {(subscriptions.data ?? []).map((s, i) => (
              <div key={i} style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginTop: 4 }}>
                {s.label ? `[${s.label}] ` : ""}{s.url} · {s.triggers.join(", ")}
                {s.node_pattern !== "*" ? ` · nodes: ${s.node_pattern}` : ""}
              </div>
            ))}
          </>
        )}

        <div className="mt-4" style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, letterSpacing: "0.08em" }}>
          DEAD-LETTER LOG
        </div>
        {(deadLetters.data ?? []).length === 0 ? (
          <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim, marginTop: 4 }}>
            none — deliveries are at-least-once with retries
          </div>
        ) : (
          (deadLetters.data ?? []).map((d, i) => (
            <div key={i} style={{ fontFamily: MONO, fontSize: 11, color: C.amber, marginTop: 4 }}>
              {d.trigger} → {d.url} · {d.error} ({d.attempts} attempts)
              {d.created_ts ? ` · ${d.created_ts.slice(0, 19)}` : ""}
            </div>
          ))
        )}
      </Section>

      {/* License */}
      <Section title="ENTERPRISE LICENSE">
        <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginBottom: 10 }}>
          Ed25519-signed, offline-verifiable — the same crypto that guards the command
          channel, and it never calls us. Expiry degrades EE to read-only; safety is
          never gated.
        </div>
        <textarea
          value={licenseJson}
          onChange={(e) => setLicenseJson(e.target.value)}
          rows={3}
          placeholder='{"license": {...}, "sig": "…"}'
          className="w-full mb-2"
          style={{ background: C.panel2, border: `1px solid ${C.line}`, borderRadius: 5, color: C.text, fontFamily: MONO, fontSize: 11, padding: 8, resize: "vertical", outline: "none" }}
        />
        <input
          value={vendorKey}
          onChange={(e) => setVendorKey(e.target.value)}
          placeholder="vendor public key (hex)"
          className="w-full mb-3"
          style={{ background: C.bg, border: `1px solid ${C.line}`, borderRadius: 5, color: C.text, fontFamily: MONO, fontSize: 11, padding: "7px 9px", outline: "none" }}
        />
        <button onClick={() => license.mutate()} disabled={!licenseJson || !vendorKey || license.isPending} style={btn({ color: C.steel, fontSize: 12 })}>
          {license.isPending ? <Loader2 size={13} className="animate-spin" /> : null} Verify license
        </button>
        {license.isError && (
          <div className="mt-2" style={{ fontFamily: MONO, fontSize: 11, color: C.red }}>
            {(license.error as Error).message}
          </div>
        )}
        {license.data && (
          <>
            <div className="mt-2" style={{ fontFamily: MONO, fontSize: 11, color: C.green }}>
              {license.data.organization} · {license.data.workspace_tier} workspace ·{" "}
              {[
                license.data.modules?.private_lab && "Private Lab",
                license.data.modules?.control_plane &&
                  `Control Plane (up to ${license.data.governed_node_ceiling} nodes)`,
              ]
                .filter(Boolean)
                .join(" + ") || "no modules"}
              {license.data.self_hosted_runner ? " · self-hosted" : ""} · expires{" "}
              {license.data.expires_at} · {license.data.features.join(", ") || "no EE features"}
            </div>
            {license.data.over_ceiling && (
              <div className="mt-1" style={{ fontFamily: MONO, fontSize: 11, color: C.amber }}>
                {license.data.live_nodes} live nodes exceed the licensed ceiling of{" "}
                {license.data.governed_node_ceiling} — a warning, never a block (safety never
                checks a license). Contact us to raise the ceiling.
              </div>
            )}
          </>
        )}
      </Section>

      <FederationVault />
    </div>
  );
}

// Federation vault (spec v2 Ch.5): TWO panes, visibly separate — "manage our
// API keys" must never be conflated with "manage who can command the
// federation". The separation is backend-enforced (separate credentials);
// the UI mirrors it structurally.
function FederationVault() {
  const creds = useQuery({ queryKey: ["vault-creds"], queryFn: api.vaultCredsHealth });
  const keys = useQuery({ queryKey: ["vault-keys"], queryFn: api.vaultSigningKeys });
  const audit = useQuery({ queryKey: ["vault-audit"], queryFn: api.vaultSigningAudit });
  return (
    <div className="flex gap-4 mb-4" data-testid="federation-vault" style={{ alignItems: "stretch" }}>
      <div className="p-4 flex-1" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
        <div style={{ fontSize: 10, fontFamily: MONO, color: C.dim, letterSpacing: "0.1em", marginBottom: 8 }}>
          FEDERATION VAULT · TOOL CREDENTIALS
        </div>
        <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, marginBottom: 10, lineHeight: 1.6 }}>
          dispensed at the sink, per-node scope, fail-closed. rotation is config;
          the plane may revoke, never grant.
        </div>
        {(creds.data?.enrolled ?? []).length === 0 ? (
          <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>no credentials enrolled</div>
        ) : (
          (creds.data?.enrolled ?? []).map((e) => (
            <div key={`${e.tool}-${e.endpoint}`} className="flex items-center gap-2 py-1" style={{ fontFamily: MONO, fontSize: 11 }}>
              <span style={{ color: e.revoked ? C.dim : C.text, textDecoration: e.revoked ? "line-through" : "none" }}>{e.tool}</span>
              <span style={{ color: C.dim, fontSize: 10 }}>v{e.version}</span>
              <span style={{ color: C.dim, fontSize: 10 }}>scope: {e.scope_nodes.join(", ") || "none"}</span>
              {e.revoked && <span style={{ color: C.red, fontSize: 10 }}>revoked</span>}
            </div>
          ))
        )}
      </div>
      <div className="p-4 flex-1" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
        <div style={{ fontSize: 10, fontFamily: MONO, color: C.dim, letterSpacing: "0.1em", marginBottom: 8 }}>
          FEDERATION VAULT · SIGNING KEYS
        </div>
        <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, marginBottom: 10, lineHeight: 1.6 }}>
          custody signs, never surrenders — the private key never leaves; every
          sign request is an audited operator action. pubkeys stay pinned in
          adapter config, never fetched from here.
        </div>
        {(keys.data ?? []).length === 0 ? (
          <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>no keys under custody</div>
        ) : (
          (keys.data ?? []).map((k) => (
            <div key={k.key_id} className="py-1" style={{ fontFamily: MONO, fontSize: 11 }}>
              <span style={{ color: C.text }}>{k.key_id}</span>
              <span style={{ color: C.dim, fontSize: 10 }}> · {k.public_key_hex.slice(0, 16)}… · signers: {k.operators.join(", ")}</span>
            </div>
          ))
        )}
        {(audit.data ?? []).length > 0 && (
          <div className="mt-2 pt-2" style={{ borderTop: `1px solid ${C.line}` }}>
            <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, letterSpacing: "0.08em", marginBottom: 4 }}>SIGN-REQUEST AUDIT</div>
            {(audit.data ?? []).slice(-5).reverse().map((a, i) => (
              <div key={i} className="flex items-center gap-2 py-0.5" style={{ fontFamily: MONO, fontSize: 10 }}>
                <span style={{ color: a.granted ? C.green : C.red }}>{a.granted ? "signed" : "refused"}</span>
                <span style={{ color: C.text }}>{a.operator}</span>
                <span style={{ color: C.dim }}>{a.key_id} · {a.payload_sha256.slice(0, 12)}…</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="p-4 mb-4" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
      <div style={{ fontSize: 10, fontFamily: MONO, color: C.dim, letterSpacing: "0.1em", marginBottom: 12 }}>
        {title}
      </div>
      {children}
    </div>
  );
}
