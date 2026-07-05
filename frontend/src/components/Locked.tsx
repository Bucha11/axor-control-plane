// The availability ladder (spec section 2): a greyed panel labelled by what
// unlocks it — the missing data is the built-in, honest upsell. Any surface
// that needs the adapter wraps its body in <Locked need="adapter">…</Locked>;
// when the connection lacks the depth, the body is replaced by the label.
import { ReactNode } from "react";
import { Lock } from "lucide-react";
import { C, MONO } from "../theme";
import { ConnectionMode, isAdapter, isConnected, useApp } from "../store";

type Need = "connection" | "adapter" | "test-bench";

function satisfied(need: Need, mode: ConnectionMode, testBench: boolean): boolean {
  if (need === "connection") return isConnected(mode);
  if (need === "adapter") return isAdapter(mode);
  return testBench;
}

const LABEL: Record<Need, string> = {
  connection: "connect an agent to unlock",
  adapter: "available with the adapter",
  "test-bench": "available on a test-bench connection",
};

export default function Locked({
  need,
  children,
  title,
}: {
  need: Need;
  children: ReactNode;
  title?: string;
}) {
  const { mode, testBench } = useApp((s) => s.connection);
  if (satisfied(need, mode, testBench)) return <>{children}</>;
  return (
    <div
      style={{
        background: C.panel,
        border: `1px dashed ${C.line}`,
        borderRadius: 8,
        padding: 24,
        display: "flex",
        alignItems: "center",
        gap: 10,
      }}
    >
      <Lock size={14} color={C.dim} />
      <div>
        {title && (
          <div style={{ fontFamily: MONO, fontSize: 12.5, color: C.mut }}>{title}</div>
        )}
        <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.dim }}>
          {LABEL[need]}
        </div>
      </div>
    </div>
  );
}

// Inline variant for a single greyed action/row.
export function LockedHint({ need }: { need: Need }) {
  return (
    <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>
      {LABEL[need]}
    </span>
  );
}
