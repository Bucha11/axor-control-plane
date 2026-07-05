// Settings — the "loud elsewhere" companion to quiet-until-wrong (spec section
// 16) plus connection and license. Notifications emit-and-route (webhook), with
// the dead-letter log surfaced honestly; a notification system that fails
// silently is worse than none.
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Check, Loader2 } from "lucide-react";
import { api } from "../api";
import { MODE_LABEL, useApp } from "../store";
import { C, MONO, btn } from "../theme";

const TRIGGERS = [
  { id: "level_transition_up", label: "degradation level rises" },
  { id: "evidence_run", label: "run completes with an EvidenceCase" },
  { id: "heat_threshold", label: "Sentinel heat crosses a threshold" },
  { id: "node_stale", label: "a node goes stale" },
];

export default function Settings() {
  const { mode, testBench } = useApp((s) => s.connection);
  const setTestBench = useApp((s) => s.setTestBench);
  const disconnect = useApp((s) => s.disconnect);

  const [url, setUrl] = useState("");
  const [selected, setSelected] = useState<string[]>(["level_transition_up", "evidence_run"]);
  const [licenseJson, setLicenseJson] = useState("");
  const [vendorKey, setVendorKey] = useState("");

  const deadLetters = useQuery({ queryKey: ["dead-letters"], queryFn: api.deadLetters });

  const subscribe = useMutation({
    mutationFn: () => api.subscribeNotifications(url, selected),
  });
  const license = useMutation({
    mutationFn: () => api.verifyLicense(licenseJson, vendorKey),
  });

  const toggle = (id: string) =>
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));

  return (
    <div style={{ maxWidth: 640, margin: "0 auto" }}>
      <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 20px" }}>Settings</h1>

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
          <div className="mt-2" style={{ fontFamily: MONO, fontSize: 11, color: C.green }}>
            {license.data.org} · {license.data.tier} · up to {license.data.node_ceiling} nodes ·
            expires {license.data.expiry} · {license.data.features.join(", ") || "no EE features"}
          </div>
        )}
      </Section>
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
