// A coach note — the learning layer. It appears only when Learn mode is on
// (opt-in, so the default experience stays quiet-until-wrong), explains what a
// surface is for in one or two sentences, and can be dismissed per-note. Toggle
// Learn mode from the header (the graduation-cap button).
import { ReactNode } from "react";
import { GraduationCap, X } from "lucide-react";
import { C, MONO } from "../theme";
import { useApp } from "../store";

export default function Coach({
  id,
  title,
  children,
}: {
  id: string;
  title: string;
  children: ReactNode;
}) {
  const learn = useApp((s) => s.learnMode);
  const dismissed = useApp((s) => s.coachDismissed);
  const dismiss = useApp((s) => s.dismissCoach);
  if (!learn || dismissed.includes(id)) return null;
  return (
    <div
      role="note"
      className="flex items-start gap-3 p-3 mb-4"
      style={{
        background: "rgba(127,168,204,0.06)",
        border: `1px solid ${C.steel}`,
        borderRadius: 8,
      }}
    >
      <GraduationCap size={15} color={C.steel} style={{ marginTop: 1, flexShrink: 0 }} />
      <div style={{ flex: 1 }}>
        <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.steel, marginBottom: 3 }}>
          {title}
        </div>
        <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, lineHeight: 1.6 }}>
          {children}
        </div>
      </div>
      <button
        onClick={() => dismiss(id)}
        aria-label="dismiss tip"
        title="dismiss this tip"
        style={{ background: "none", border: "none", cursor: "pointer", color: C.dim, padding: 0, flexShrink: 0 }}
      >
        <X size={13} />
      </button>
    </div>
  );
}
