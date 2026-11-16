// The config beside the form, editable (Policy's split view). Both ways:
// a click in the form highlights where that lands in the text (`focus`, a key
// path such as ["sinks", "send_email", "driving_args"]); a hand edit that parses
// and that the plane accepts (POST /v1/config/check — the same reading Replay
// and Safe to ship? use) goes back into the form. Until then the form keeps
// what it had and the error says where.
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { C, MONO } from "../theme";

const LINE = 17;  // px — the backdrop and the textarea must agree on it
const PAD = 10;

/** The lines a key path spans in 2-space-indented JSON: the key's line to its
 * closing bracket. A key that is not there yields its nearest parent. */
export function keyLines(text: string, path: string[]): [number, number] | null {
  const lines = text.split("\n");
  let from = 0;
  let end = lines.length - 1;
  let found: [number, number] | null = null;
  for (let d = 0; d < path.length; d++) {
    const ind = "  ".repeat(d + 1);
    const at = lines.findIndex((l, i) => i >= from && i <= end && l.startsWith(`${ind}"${path[d]}":`));
    if (at < 0) break;
    let close = at;
    if (/[[{]\s*$/.test(lines[at])) {
      const shut = lines.findIndex((l, i) => i > at && (l.startsWith(`${ind}}`) || l.startsWith(`${ind}]`)));
      close = shut < 0 ? at : shut;
    }
    found = [at, close];
    from = at + 1;
    end = close;
  }
  return found;
}

/** Where JSON.parse stopped, as a line number — engines say "position N". */
function errorLine(text: string, message: string): number | null {
  const pos = /position (\d+)/.exec(message)?.[1];
  if (pos) return text.slice(0, Number(pos)).split("\n").length;
  const line = /line (\d+)/.exec(message)?.[1];
  return line ? Number(line) : null;
}

export default function ConfigText({ text, focus, onApply }: {
  text: string; focus: string[] | null; onApply: (config: Record<string, unknown>) => void;
}) {
  const [draft, setDraft] = useState(text);
  const [typing, setTyping] = useState(false);
  const [error, setError] = useState<{ msg: string; line: number | null } | null>(null);
  const [checking, setChecking] = useState(false);
  const area = useRef<HTMLTextAreaElement>(null);
  const back = useRef<HTMLPreElement>(null);
  const file = useRef<HTMLInputElement>(null);

  // the form changed and nobody is typing here: show what the form says
  useEffect(() => { if (!typing) { setDraft(text); setError(null); } }, [text, typing]);

  const lit = useMemo(() => (focus ? keyLines(draft, focus) : null), [draft, focus]);
  // a click in the form scrolls its key into view
  useEffect(() => {
    if (!lit || !area.current) return;
    const top = lit[0] * LINE;
    const el = area.current;
    if (top < el.scrollTop || top > el.scrollTop + el.clientHeight - 3 * LINE) {
      el.scrollTop = Math.max(0, top - 3 * LINE);
    }
  }, [lit]);

  const apply = async (value: string) => {
    let parsed: unknown;
    try {
      parsed = JSON.parse(value);
    } catch (e) {
      const msg = (e as Error).message;
      setError({ msg, line: errorLine(value, msg) });
      return;
    }
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      setError({ msg: "the config is a JSON object", line: 1 });
      return;
    }
    setChecking(true);
    try {
      await api.checkConfig(parsed);
      setError(null);
      onApply(parsed as Record<string, unknown>);
    } catch (e) {
      // the plane's 400 is {"error": "..."}; the value it names is found in the text
      let msg = (e as Error).message.replace(/^\d+\s*/, "");
      try { msg = (JSON.parse(msg) as { error?: string; detail?: string }).error ?? msg; } catch { /* plain */ }
      const named = /: '([^']+)' is not|got '([^']+)'/.exec(msg);
      const needle = named ? `"${named[1] ?? named[2]}"` : null;
      const at = needle ? value.split("\n").findIndex((l) => l.includes(needle)) : -1;
      setError({ msg, line: at >= 0 ? at + 1 : null });
    } finally {
      setChecking(false);
    }
  };
  // a moment after the last key
  useEffect(() => {
    if (!typing) return;
    const t = setTimeout(() => void apply(draft), 600);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft, typing]);

  const lines = draft.split("\n");
  const font: React.CSSProperties = {
    fontFamily: MONO, fontSize: 11.5, lineHeight: `${LINE}px`, padding: PAD, margin: 0,
    whiteSpace: "pre", tabSize: 2, overflowWrap: "normal",
  };
  return (
    <div data-testid="config-text" className="flex flex-col" style={{ height: "100%", minHeight: 0 }}>
      <div className="flex items-center gap-3 mb-2" style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>
        <span style={{ color: C.text }}>axor.config.json</span>
        <span>{checking ? "checking…" : error ? "" : typing ? "" : "edit here or in the form — both stay in step"}</span>
        <span style={{ flex: 1 }} />
        <input ref={file} type="file" accept=".json,application/json" style={{ display: "none" }}
          data-testid="config-file"
          onChange={(e) => {
            const f = e.target.files?.[0];
            e.target.value = "";
            if (!f) return;
            void f.text().then((t) => { setTyping(true); setDraft(t); void apply(t); });
          }} />
        <button onClick={() => file.current?.click()}
          style={{ background: "none", border: "none", padding: 0, cursor: "pointer", fontFamily: MONO, fontSize: 11, color: C.steel }}>
          open a file…
        </button>
      </div>
      <div style={{ position: "relative", flex: 1, minHeight: 280, border: `1px solid ${error ? C.red : C.line}`,
                    borderRadius: 6, background: C.panel2, overflow: "hidden" }}>
        <pre ref={back} aria-hidden style={{ ...font, position: "absolute", inset: 0, color: "transparent",
                                             overflow: "hidden", pointerEvents: "none" }}>
          {lines.map((l, i) => {
            const on = lit && i >= lit[0] && i <= lit[1];
            const bad = error?.line === i + 1;
            return (
              <div key={i} style={{ background: bad ? "rgba(229,72,77,0.18)" : on ? "rgba(127,168,204,0.16)" : "none",
                                    borderLeft: `2px solid ${bad ? C.red : on ? C.steel : "transparent"}`, marginLeft: -PAD, paddingLeft: PAD - 2 }}>
                {l || " "}
              </div>
            );
          })}
        </pre>
        <textarea ref={area} value={draft} spellCheck={false} aria-label="the config, as JSON"
          onChange={(e) => { setTyping(true); setDraft(e.target.value); }}
          onBlur={() => { if (!error) setTyping(false); }}
          onScroll={(e) => { if (back.current) { back.current.scrollTop = e.currentTarget.scrollTop; back.current.scrollLeft = e.currentTarget.scrollLeft; } }}
          style={{ ...font, position: "absolute", inset: 0, width: "100%", height: "100%", resize: "none",
                   background: "transparent", color: C.text, border: "none", outline: "none", overflow: "auto",
                   caretColor: C.text }} />
      </div>
      {error && (
        <div data-testid="config-error" className="mt-2" style={{ fontFamily: MONO, fontSize: 11, color: C.red, lineHeight: 1.5 }}>
          {error.line ? `line ${error.line}: ` : ""}{error.msg} — the form keeps what it had until this is fixed
        </div>
      )}
    </div>
  );
}
