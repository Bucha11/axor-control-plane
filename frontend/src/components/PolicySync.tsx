// The tool policy is the workspace's (/v1/policy), not this browser's. It
// lived only in the persisted store, so another person — or the same one on
// another machine — got it rebuilt from the agent's runs: a refund tool as
// "reads", the ticket's outside-text field and the email's deciding argument
// gone, while the guard enforced the rules it was started with.
//
// The workspace's copy is the only one that counts. On load it replaces this
// browser's — nothing kept locally is merged into it or handed over: a copy
// left in the browser put back a tool removed since, or one added while the
// read had failed (QA-420, QA-421). Only edits made in this tab while the read
// was on its way go on top of it: the difference from what the tab held when
// it asked, removals included. Every change after that is saved, debounced,
// and only while this tab's copy is the workspace's (policyRead). A principal
// without the operate scope saves nothing — the screens offer no edits — and
// a save the plane refuses for any reason is said, never left on screen as
// though it were saved (QA-424).
import { useEffect, useRef } from "react";
import { api, ApiError } from "../api";
import { useApp, type ToolClass } from "../store";
import { errorWords } from "../refusal";
import { useHasScope } from "../team";
import { C, MONO, btn } from "../theme";

const SAVE_AFTER_MS = 800;

type Tools = Record<string, ToolClass>;

/** `held` with the edits that turned `before` into `now` put on top of it. */
function withEdits(held: Tools, before: Tools, now: Tools): Tools {
  const out = { ...held };
  for (const name of Object.keys(before)) if (!(name in now)) delete out[name];
  for (const [name, c] of Object.entries(now)) if (before[name] !== c) out[name] = c;
  return out;
}

// A failed read is not "no policy": saving then would put this browser's copy
// over the workspace's (QA-231). A save the plane refuses because someone else
// saved since this tab read the policy is not "saved" either (QA-212). Either
// way nothing more is saved until the policy is read again.
export default function PolicySync() {
  const applying = useRef(false);
  // the version of the workspace's policy this tab read, sent with each save
  const version = useRef<number | undefined>(undefined);
  const loads = useApp((s) => s.policyLoads);
  const mayWrite = useHasScope()("operate");
  const writes = useRef(mayWrite);
  writes.current = mayWrite;
  useEffect(() => {
    let cancelled = false;
    useApp.setState({ policyRead: false });
    const atMount = useApp.getState().toolPolicy;
    void api.getPolicy().then((saved) => {
      if (cancelled) return;
      version.current = saved.version;
      const local = useApp.getState().toolPolicy;
      const edited = local !== atMount && writes.current;
      const next = edited ? withEdits(saved.tools, atMount, local) : saved.tools;
      applying.current = true;
      useApp.setState((s) => ({ toolPolicy: next, policyRead: true, policyApplied: s.policyApplied + 1 }));
      applying.current = false;
      if (edited) save(next);
    }).catch((e: unknown) => {
      if (cancelled) return;
      useApp.getState().setPolicyNotice({ kind: "read_failed",
        words: `could not read the workspace's policy (${errorWords((e as Error).message)}) — `
          + "nothing changed here is saved until it is read; retry" });
    });
    return () => { cancelled = true; };
  }, [loads]);

  const save = (tools: Tools) => {
    if (!writes.current) return;
    void api.putPolicy(tools, version.current).then((r) => { version.current = r.version; })
      .catch((e: unknown) => {
        useApp.setState({ policyRead: false });
        if (e instanceof ApiError && e.status === 409 && e.code === "stale") {
          useApp.getState().setPolicyNotice({ kind: "stale", words: e.message });
        } else {
          useApp.getState().setPolicyNotice({ kind: "not_saved",
            words: `the change was not saved (${errorWords((e as Error).message)}) — reload to see the workspace's policy` });
        }
      });
  };

  useEffect(() => {
    let timer: number | undefined;
    let last: Tools = useApp.getState().toolPolicy;
    const unsub = useApp.subscribe((s) => {
      if (s.toolPolicy === last) return;
      last = s.toolPolicy;
      if (!s.policyRead || applying.current) return;
      window.clearTimeout(timer);
      timer = window.setTimeout(() => { if (useApp.getState().policyRead) save(last); }, SAVE_AFTER_MS);
    });
    return () => { unsub(); window.clearTimeout(timer); };
  }, []);
  return null;
}

/** Said on Policy: the workspace's policy could not be read, or someone saved
 *  it since this tab read it — with the way to read it again. */
export function PolicyNotice() {
  const notice = useApp((s) => s.policyNotice);
  const reload = useApp((s) => s.reloadPolicy);
  if (!notice) return null;
  return (
    <div data-testid={notice.kind === "stale" ? "policy-stale" : notice.kind === "not_saved" ? "policy-not-saved" : "policy-read-failed"} role="alert"
      className="mb-4 p-3 flex items-center gap-3 flex-wrap"
      style={{ maxWidth: 820, margin: "0 auto 16px", border: `1px solid ${notice.kind === "stale" ? C.amber : C.red}`,
               borderRadius: 8, fontFamily: MONO, fontSize: 11.5, color: C.text, lineHeight: 1.6 }}>
      <span style={{ flex: 1, minWidth: 260 }}>{notice.words}</span>
      <button data-testid="policy-reload" onClick={reload} style={btn({ fontSize: 11, padding: "4px 12px" })}>
        {notice.kind === "read_failed" ? "retry" : "reload"}
      </button>
    </div>
  );
}
