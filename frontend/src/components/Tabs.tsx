// Tabs — one row of views of the same place, underlined, not a row of pills.
// Pills are for status and filters; a choice of view is a tab.
import { C, MONO } from "../theme";

export interface TabItem<T extends string> {
  id: T;
  label: string;
  testId?: string;
}

export default function Tabs<T extends string>({ items, active, onChange, size = "md", style }: {
  items: TabItem<T>[];
  active: T;
  onChange: (id: T) => void;
  size?: "md" | "lg";
  style?: React.CSSProperties;
}) {
  const fontSize = size === "lg" ? 14 : 12;
  return (
    <div role="tablist" className="flex" style={{
      gap: size === "lg" ? 24 : 18, borderBottom: `1px solid ${C.line}`, overflowX: "auto", ...style,
    }}>
      {items.map((t) => {
        const on = t.id === active;
        return (
          <button key={t.id} role="tab" aria-selected={on} data-testid={t.testId}
            onClick={() => onChange(t.id)}
            style={{
              background: "none", border: "none", cursor: "pointer", whiteSpace: "nowrap",
              padding: size === "lg" ? "8px 0 10px" : "6px 0 8px", marginBottom: -1,
              fontFamily: MONO, fontSize, fontWeight: on ? 700 : 400,
              color: on ? C.text : C.dim,
              borderBottom: `2px solid ${on ? C.steel : "transparent"}`,
            }}>
            {t.label}
          </button>
        );
      })}
    </div>
  );
}
