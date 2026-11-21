// A command to run on the developer's machine — copyable, and nothing else.
// The connection (plane address and key) is set once when the machine is
// connected, so it is not repeated in every snippet; a machine that is not
// connected yet gets one link to do that.
import { useState } from "react";
import { Check, Copy } from "lucide-react";
import { navigate } from "../router";
import { C, MONO } from "../theme";

export function MachineCommand({ cmd, testId, connectHint = true }: {
  cmd: string; testId?: string; connectHint?: boolean;
}) {
  const [copied, setCopied] = useState(false);
  return (
    <div>
      <div style={{ position: "relative" }}>
        <pre data-testid={testId} style={{ background: C.panel2, border: `1px solid ${C.line}`, borderRadius: 6,
          padding: "10px 34px 10px 12px", fontFamily: MONO, fontSize: 11.5, color: C.text, margin: 0,
          whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{cmd}</pre>
        <button aria-label="copy" onClick={() => {
          void navigator.clipboard?.writeText(cmd); setCopied(true); setTimeout(() => setCopied(false), 1500);
        }} style={{ position: "absolute", top: 8, right: 8, background: "none", border: "none", color: C.dim, cursor: "pointer" }}>
          {copied ? <Check size={12} /> : <Copy size={12} />}
        </button>
      </div>
      {connectHint && (
        <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 5 }}>
          on a machine connected to this plane ·{" "}
          <button onClick={() => navigate("connect", { for: "coding" })} style={{ background: "none", border: "none",
            padding: 0, cursor: "pointer", fontFamily: MONO, fontSize: 10.5, color: C.steel }}>
            not connected yet?
          </button>
        </div>
      )}
    </div>
  );
}

/** One short line, and the rest behind "how it works". */
export function HowItWorks({ children, testId }: { children: React.ReactNode; testId?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div style={{ marginTop: 4 }}>
      <button data-testid={testId} onClick={() => setOpen(!open)} style={{ background: "none", border: "none", padding: 0,
        cursor: "pointer", fontFamily: MONO, fontSize: 10.5, color: C.dim }}>
        {open ? "▾" : "▸"} how it works
      </button>
      {open && (
        <ul style={{ margin: "6px 0 0", paddingLeft: 18, fontSize: 12.5, color: C.mut, lineHeight: 1.6 }}>
          {children}
        </ul>
      )}
    </div>
  );
}
