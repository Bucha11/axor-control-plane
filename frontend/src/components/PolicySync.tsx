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
import { api } from "../api";
import { useApp, type ToolClass } from "../store";

const SAVE_AFTER_MS = 800;

export default function PolicySync() {
  const loaded = useRef(false);
  const applying = useRef(false);
  useEffect(() => {
    let cancelled = false;
    const atMount = useApp.getState().toolPolicy;
    void api.getPolicy().then((saved) => {
      if (cancelled) return;
      const local = useApp.getState().toolPolicy;
      // edited while the workspace's policy was on its way (a tool added in
      // the first moment): those edits are kept on top of it, not overwritten
      const editedMeanwhile = local !== atMount;
      const merged = editedMeanwhile ? { ...saved.tools, ...local } : saved.tools;
      if (Object.keys(saved.tools).length) {
        applying.current = true;
        useApp.setState({ toolPolicy: merged });
        applying.current = false;
        if (editedMeanwhile) void api.putPolicy(merged).catch(() => undefined);
      } else if (Object.keys(local).length) {
        void api.putPolicy(local).catch(() => undefined);
      }
      loaded.current = true;
    }).catch(() => { loaded.current = true; });   // no backend: stay local
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    let timer: number | undefined;
    let last: Record<string, ToolClass> = useApp.getState().toolPolicy;
    const unsub = useApp.subscribe((s) => {
      if (s.toolPolicy === last) return;
      last = s.toolPolicy;
      if (!loaded.current || applying.current) return;
      window.clearTimeout(timer);
      timer = window.setTimeout(() => { void api.putPolicy(last).catch(() => undefined); }, SAVE_AFTER_MS);
    });
    return () => { unsub(); window.clearTimeout(timer); };
  }, []);
  return null;
}
