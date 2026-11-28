// The tool policy is the workspace's (/v1/policy), not this browser's. It
// lived only in the persisted store, so another person — or the same one on
// another machine — got it rebuilt from the agent's runs: a refund tool as
// "reads", the ticket's outside-text field and the email's deciding argument
// gone, while the guard enforced the rules it was started with.
//
// On load the workspace's policy replaces the local one (a browser that holds
// one the workspace does not yet have hands it over once); every change after
// that is saved, debounced. A read-only principal's save is refused by the
// backend, and the local copy is simply not kept.
import { useEffect, useRef } from "react";
import { api, ApiError } from "../api";
import { useApp, type ToolClass } from "../store";
import { errorWords } from "../refusal";
import { C, MONO, btn } from "../theme";

const SAVE_AFTER_MS = 800;

// A failed read is not "no policy": saving then would put this browser's copy
// over the workspace's (QA-231). A save the plane refuses because someone else
// saved since this tab read the policy is not "saved" either (QA-212). Either
// way nothing more is saved until the policy is read again.
export default function PolicySync() {
  const loaded = useRef(false);
  const applying = useRef(false);
  // the version of the workspace's policy this tab read, sent with each save
  const version = useRef<number | undefined>(undefined);
  const loads = useApp((s) => s.policyLoads);
  useEffect(() => {
    let cancelled = false;
    loaded.current = false;
    const atMount = useApp.getState().toolPolicy;
    void api.getPolicy().then((saved) => {
      if (cancelled) return;
      version.current = saved.version;
      const local = useApp.getState().toolPolicy;
      // edited while the workspace's policy was on its way (a tool added in
      // the first moment): those edits are kept on top of it, not overwritten
      const editedMeanwhile = local !== atMount;
      const merged = editedMeanwhile ? { ...saved.tools, ...local } : saved.tools;
      if (Object.keys(saved.tools).length) {
        applying.current = true;
        useApp.setState({ toolPolicy: merged });
        applying.current = false;
        if (editedMeanwhile) save(merged);
      } else if (Object.keys(local).length) {
        save(local);
      }
      loaded.current = true;
    }).catch((e: unknown) => {
      if (cancelled) return;
      useApp.getState().setPolicyNotice({ kind: "read_failed",
        words: `could not read the workspace's policy (${errorWords((e as Error).message)}) — `
          + "nothing changed here is saved until it is read; retry" });
    });
    return () => { cancelled = true; };
  }, [loads]);

  const save = (tools: Record<string, ToolClass>) => {
    void api.putPolicy(tools, version.current).then((r) => { version.current = r.version; })
      .catch((e: unknown) => {
        if (e instanceof ApiError && e.status === 409 && e.code === "stale") {
          loaded.current = false;
          useApp.getState().setPolicyNotice({ kind: "stale", words: e.message });
        }
      });
  };

  useEffect(() => {
    let timer: number | undefined;
    let last: Record<string, ToolClass> = useApp.getState().toolPolicy;
    const unsub = useApp.subscribe((s) => {
      if (s.toolPolicy === last) return;
      last = s.toolPolicy;
      if (!loaded.current || applying.current) return;
      window.clearTimeout(timer);
      timer = window.setTimeout(() => { if (loaded.current) save(last); }, SAVE_AFTER_MS);
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
    <div data-testid={notice.kind === "stale" ? "policy-stale" : "policy-read-failed"} role="alert"
      className="mb-4 p-3 flex items-center gap-3 flex-wrap"
      style={{ maxWidth: 820, margin: "0 auto 16px", border: `1px solid ${notice.kind === "stale" ? C.amber : C.red}`,
               borderRadius: 8, fontFamily: MONO, fontSize: 11.5, color: C.text, lineHeight: 1.6 }}>
      <span style={{ flex: 1, minWidth: 260 }}>{notice.words}</span>
      <button data-testid="policy-reload" onClick={reload} style={btn({ fontSize: 11, padding: "4px 12px" })}>
        {notice.kind === "stale" ? "reload" : "retry"}
      </button>
    </div>
  );
}
