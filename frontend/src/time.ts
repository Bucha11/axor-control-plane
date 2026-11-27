// One way to show a stored time, app-wide: the viewer's local time, as Replay
// shows it. Times are stored as UTC ISO-8601 strings; cutting the string
// ("2026-11-26T20:07" → "2026-11-26 20:07") showed UTC with no label, beside
// screens in local time (QA-037).

/** "2026-11-26 21:07" (with `seconds`, "2026-11-26 21:07:42") in local time. */
export function localTime(ts: string | null | undefined, seconds = false): string {
  if (!ts) return "";
  // a stamp without a zone is UTC, as every stored one is
  const d = new Date(/T.*([zZ]|[+-]\d\d:?\d\d)$/.test(ts) || !ts.includes("T") ? ts : `${ts}Z`);
  if (Number.isNaN(d.getTime())) return ts;
  const p = (n: number) => String(n).padStart(2, "0");
  const day = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  const time = `${p(d.getHours())}:${p(d.getMinutes())}${seconds ? `:${p(d.getSeconds())}` : ""}`;
  return `${day} ${time}`;
}

/** "2026-11-26": the local day of a stored time. A bare calendar date
 *  ("2026-12-01", no time) is a day already, not an instant: returned as is. */
export function localDate(ts: string | null | undefined): string {
  if (!ts || !ts.includes("T")) return ts ?? "";
  return localTime(ts).slice(0, 10);
}
