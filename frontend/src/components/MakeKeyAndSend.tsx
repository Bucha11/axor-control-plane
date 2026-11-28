// A command the plane refused for want of an operator key: for an owner or
// admin, this browser's key is made right here and the command sent — on a
// new workspace pause, stop and cap otherwise sent people to Settings, or (the
// pair pane) said nothing at all (QA-501).
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";
import { useCanAdmin } from "../team";
import { C, MONO, btn } from "../theme";

/** Whether a refusal is the one this offers to fix. */
export function needsKey(e: unknown): boolean {
  return e instanceof ApiError && e.code === "operator_key_needed";
}

export default function MakeKeyAndSend({ what, send, label }: { what: string; send: () => void; label?: string }) {
  const admin = useCanAdmin();
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  if (!admin) return null;
  const go = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api.registerOperatorKey(navigator.platform || "browser");
      for (const k of ["operators", "op-key-local", "operator-keys"]) void qc.invalidateQueries({ queryKey: [k] });
      send();
    } catch (e) {
      setErr(`could not make the key — ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };
  return (
    <span style={{ fontFamily: MONO, fontSize: 11 }}>
      <button data-testid="make-key-send" disabled={busy} onClick={() => void go()}
        style={btn({ color: C.text, borderColor: C.steel, fontSize: 11, padding: "2px 10px", marginTop: 4 })}>
        {busy ? "making the key…" : label ?? `make this browser's operator key and send ${what}`}
      </button>
      {err && <span data-testid="make-key-error" style={{ color: C.red, marginLeft: 6 }}>{err}</span>}
    </span>
  );
}
