// One way to show a stored time, app-wide: the viewer's local time, as Replay
// shows it. Times are stored as UTC ISO-8601 strings; cutting the string
// ("2026-11-26T20:07" → "2026-11-26 20:07") showed UTC with no label, beside
// screens in local time (QA-037).

/** The instant an ISO-8601 stamp names, or null for anything else. The browser
 *  reads "seq:3" as a date in 2001 (QA-081); a stamp that is not ISO is never
 *  shown or sorted as one. */
export function isoDate(ts: string | null | undefined): Date | null {
  if (!ts || !/^\d{4}-\d\d-\d\d([T ]\d\d:\d\d(:\d\d(\.\d+)?)?([zZ]|[+-]\d\d:?\d\d)?)?$/.test(ts)) return null;
  const t = ts.replace(" ", "T");
  // a stamp without a zone is UTC, as every stored one is
  const d = new Date(/T.*([zZ]|[+-]\d\d:?\d\d)$/.test(t) || !t.includes("T") ? t : `${t}Z`);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "UTC+4", "UTC+5:45", "UTC": the viewer's zone at that instant. Said beside
 *  every local time: the shared and exported copies of the same run say UTC,
 *  and the owner's screens said a local time with no zone (QA-385). */
export function zoneOf(d: Date): string {
  const off = -d.getTimezoneOffset();
  if (off === 0) return "UTC";
  const a = Math.abs(off);
  return `UTC${off > 0 ? "+" : "-"}${Math.floor(a / 60)}${a % 60 ? `:${String(a % 60).padStart(2, "0")}` : ""}`;
}

/** "2026-11-26 21:07 UTC+1" (with `seconds`, "2026-11-26 21:07:42 UTC+1") in
 *  local time, with its zone. A stamp that is not ISO-8601 is returned as
 *  written, never as a date. */
export function localTime(ts: string | null | undefined, seconds = false): string {
  if (!ts) return "";
  const d = isoDate(ts);
  if (!d) return ts;
  const p = (n: number) => String(n).padStart(2, "0");
  const day = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  const time = `${p(d.getHours())}:${p(d.getMinutes())}${seconds ? `:${p(d.getSeconds())}` : ""}`;
  return `${day} ${time} ${zoneOf(d)}`;
}

/** "2026-11-26": the local day of a stored time. A bare calendar date
 *  ("2026-12-01", no time) is a day already, not an instant: returned as is. */
export function localDate(ts: string | null | undefined): string {
  if (!ts || !ts.includes("T")) return ts ?? "";
  return localTime(ts).slice(0, 10);
}
