// "Find them in your code": the Config Builder's scanner (axor-wrap on the
// backend, POST /v1/wrap/scan) wherever an agent's tools are being listed.
// It finds the tool functions and guesses what each does; every guess lands in
// the shared tool record (store.toolPolicy) for the person to correct. What it
// cannot find is where an HTTP tool lives — that stays a field to fill.
import { useState } from "react";
import { Upload } from "lucide-react";
import { api, type WrapTool } from "../api";
import { useApp } from "../store";
import { C, MONO, btn } from "../theme";
import { filesFromList } from "../tabs/ConfigBuilder";

export default function ScanTools({ onFound, label = "find them in your code", testId = "scan-tools" }: {
  onFound?: (tools: WrapTool[]) => void; label?: string; testId?: string;
}) {
  const setToolClass = useApp((s) => s.setToolClass);
  const known = useApp((s) => s.toolPolicy);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const scan = async (files: FileList | null) => {
    setNote(null);
    const picked = await filesFromList(files);
    if (picked.length === 0) { setNote("no .py files — the scanner reads Python sources"); return; }
    setBusy(true);
    try {
      const res = await api.wrapScan(picked);
      for (const t of res.tools) {
        // a tool already classified keeps the person's call; a new one gets the guess
        if (known[t.id]) {
          setToolClass(t.id, { source: t.source, args_schema: t.args_schema });
          continue;
        }
        setToolClass(t.id, {
          klass: t.guess.default_class === "UNKNOWN" ? "READ" : t.guess.default_class,
          outside: t.guess.untrusted_fields.length > 0,
          source: t.source, args_schema: t.args_schema, driving: t.guess.driving_args,
        });
      }
      setNote(res.tools.length ? `found ${res.tools.length} tool${res.tools.length === 1 ? "" : "s"} — check what each does`
                               : "no tools found in those files — the scanner finds LangChain @tool / Tool(...), FastMCP @mcp.tool(), and {name, description, input_schema} tool lists. Tools your agent calls over HTTP by URL are not in code it can see: add each one by hand below");
      onFound?.(res.tools);
    } catch (e) {
      const msg = (e as Error).message;
      setNote(msg.startsWith("501") ? "the scanner is not installed on this plane — list the tools by hand" : msg);
    } finally {
      setBusy(false);
    }
  };
  return (
    <span className="inline-flex items-center gap-2 flex-wrap">
      <label style={btn({ color: C.steel, borderColor: C.steel, fontSize: 11.5, padding: "5px 12px", cursor: "pointer" })}>
        <Upload size={12} /> {busy ? "reading…" : label}
        <input type="file" multiple accept=".py" data-testid={testId} style={{ display: "none" }}
          onChange={(e) => void scan(e.target.files)} />
      </label>
      {note && <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut }}>{note}</span>}
    </span>
  );
}
