import { useEffect, useRef, useState } from "react";
import { C, MONO, btn } from "../theme";

// In-app stand-in for window.confirm / window.prompt (QA-034): same promise
// semantics — confirm → true/false, prompt → the text ("" if left empty) or
// null on cancel — without blocking the page. <AskHost/> is mounted once.

type Req = { message: string; input: string | null; resolve: (v: string | null) => void };

let show: ((r: Req | null) => void) | null = null;
let pending: Req | null = null;

function open(message: string, input: string | null): Promise<string | null> {
  return new Promise((resolve) => {
    pending?.resolve(null); // a newer question cancels an unanswered one
    pending = { message, input, resolve };
    show?.(pending);
  });
}

export const askConfirm = (message: string): Promise<boolean> =>
  open(message, null).then((v) => v != null);

export const askPrompt = (message: string, defaultValue = ""): Promise<string | null> =>
  open(message, defaultValue);

export function AskHost() {
  const [req, setReq] = useState<Req | null>(pending);
  const [text, setText] = useState(pending?.input ?? "");
  const box = useRef<HTMLFormElement>(null);

  useEffect(() => {
    show = (r) => { setText(r?.input ?? ""); setReq(r); };
    return () => { show = null; };
  }, []);

  function done(r: Req, v: string | null) {
    if (pending === r) pending = null;
    setReq(null);
    r.resolve(v);
  }

  useEffect(() => {
    if (!req) return;
    const el = box.current?.querySelector<HTMLElement>("input, button[type=submit]");
    el?.focus();
    if (el instanceof HTMLInputElement) el.select();
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") { e.preventDefault(); done(req, null); } };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [req]);

  if (!req) return null;
  return (
    <div onClick={() => done(req, null)}
      style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.55)", zIndex: 200,
        display: "flex", alignItems: "center", justifyContent: "center", padding: 16 }}>
      <form ref={box} role="dialog" aria-modal="true" aria-label="confirm" data-testid="ask"
        onClick={(e) => e.stopPropagation()}
        onSubmit={(e) => { e.preventDefault(); done(req, req.input == null ? "" : text); }}
        style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8, padding: 18,
          display: "flex", flexDirection: "column", gap: 12, width: 420, maxWidth: "100%" }}>
        <div style={{ fontFamily: MONO, fontSize: 12, color: C.text, lineHeight: 1.6, whiteSpace: "pre-wrap" }}>
          {req.message}
        </div>
        {req.input != null && (
          <input aria-label="answer" value={text} onChange={(e) => setText(e.target.value)}
            style={{ background: C.panel2, border: `1px solid ${C.line}`, borderRadius: 5, color: C.text,
              fontFamily: MONO, fontSize: 12, padding: "7px 10px", outline: "none" }} />
        )}
        <div className="flex items-center gap-2">
          <button type="submit" style={btn({ color: C.steel, borderColor: C.steel, fontSize: 11, padding: "5px 14px" })}>
            ok
          </button>
          <button type="button" onClick={() => done(req, null)} style={btn({ color: C.dim, fontSize: 10.5, padding: "5px 10px" })}>
            cancel
          </button>
        </div>
      </form>
    </div>
  );
}
