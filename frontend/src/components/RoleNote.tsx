// One line where a control would be for a role without the scope: who the
// person is and who can (QA-175, QA-196, QA-152, QA-213, QA-064). Pair it with
// useHasScope(): the control itself is not offered.
import { C, MONO } from "../theme";
import { whoCan, whoIs } from "../refusal";
import { useScopes } from "../team";

export default function RoleNote({ need, what, testid = "role-note" }: {
  need: "ingest" | "operate" | "admin"; what: string; testid?: string;
}) {
  const have = useScopes() ?? [];
  return (
    <div data-testid={testid} style={{ fontFamily: MONO, fontSize: 11, color: C.dim, lineHeight: 1.6 }}>
      as {whoIs(have)} you can&apos;t {what} — {whoCan(need)} can
    </div>
  );
}
