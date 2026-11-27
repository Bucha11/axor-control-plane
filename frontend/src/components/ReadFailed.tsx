// A read that failed is said as one, with a retry — never shown as an empty
// list ("no agents yet", "no team policy yet") that invites acting on it
// (QA-231, QA-233, QA-234).
import { errorWords } from "../refusal";
import { C, MONO, btn } from "../theme";

export default function ReadFailed({ what, error, retry }: {
  what: string; error: unknown; retry: () => void;
}) {
  return (
    <div data-testid="read-failed" role="alert" className="flex items-center gap-3 flex-wrap"
      style={{ fontFamily: MONO, fontSize: 12, color: C.text, border: `1px solid ${C.red}`, borderRadius: 8,
               padding: "10px 12px", margin: "0 0 12px", lineHeight: 1.6 }}>
      <span style={{ flex: 1, minWidth: 240 }}>
        could not load {what} — {errorWords((error as Error | null)?.message ?? "the request failed")}
      </span>
      <button data-testid="read-failed-retry" onClick={retry}
        style={btn({ fontSize: 11, padding: "4px 12px" })}>retry</button>
    </div>
  );
}
