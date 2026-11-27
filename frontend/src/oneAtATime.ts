// A button that makes something (a key, a token, a subscription) sends it once
// per click until the answer is back. `disabled={m.isPending}` alone let the
// second click of a double-click through: react-query puts `isPending` on the
// screen a tick after `mutate`, and a second click lands inside that tick —
// two keys minted, the second secret never shown (QA-017).
import { useRef } from "react";

export function useOneAtATime<T>(m: { mutate: (v: T, o?: { onSettled?: () => void }) => void }): (v: T) => void {
  const busy = useRef(false);
  return (v: T) => {
    if (busy.current) return;
    busy.current = true;
    m.mutate(v, { onSettled: () => { busy.current = false; } });
  };
}
