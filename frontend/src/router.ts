// Tiny hash router — deep links matter (share permalinks, "open replay at run X
// step N", "regressed row -> replay at divergence"). No external dependency:
// the URL hash is the single source of truth, and useRoute subscribes to it.
import { useEffect, useState } from "react";

export interface Route {
  // path segments after '#/', e.g. ["replay", "run_7c31"]
  segments: string[];
  // query params after '?', e.g. { cursor: "6" }
  query: Record<string, string>;
  raw: string;
}

/** decodeURIComponent that keeps a malformed escape as written: a link cut
 *  short by a mail client ("%E0%A4%A") threw a URIError while rendering and
 *  blanked the whole app (QA-043). */
export function safeDecode(s: string): string {
  try {
    return decodeURIComponent(s);
  } catch {
    return s;
  }
}

function parse(hash: string): Route {
  const raw = hash.replace(/^#\/?/, "");
  const [pathPart, queryPart] = raw.split("?");
  const segments = pathPart ? pathPart.split("/").filter(Boolean) : [];
  const query: Record<string, string> = {};
  if (queryPart) {
    for (const pair of queryPart.split("&")) {
      const [k, v] = pair.split("=");
      if (k) query[safeDecode(k)] = safeDecode(v ?? "");
    }
  }
  return { segments, query, raw };
}

export function navigate(
  path: string,
  query?: Record<string, string | number | undefined>,
): void {
  let hash = `#/${path.replace(/^\//, "")}`;
  if (query) {
    const parts = Object.entries(query)
      .filter(([, v]) => v !== undefined && v !== "")
      .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`);
    if (parts.length) hash += `?${parts.join("&")}`;
  }
  window.location.hash = hash;
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parse(window.location.hash));
  useEffect(() => {
    const onChange = (): void => setRoute(parse(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}
