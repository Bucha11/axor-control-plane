// A themed hover/focus tooltip — replaces the browser's slow, unstyled native
// `title=` on the actions that aren't self-evident. Zero dependency (the project
// hand-rolls its UI), accessible (role="tooltip" + aria-describedby, shows on
// keyboard focus too), and it never intercepts the pointer so the wrapped
// control stays clickable.
import { ReactNode, useId, useState } from "react";
import { C, MONO } from "../theme";

export default function Tooltip({
  content,
  children,
  side = "top",
  width = 240,
}: {
  content: ReactNode;
  children: ReactNode;
  side?: "top" | "bottom";
  width?: number;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  if (!content) return <>{children}</>;
  return (
    <span
      style={{ position: "relative", display: "inline-flex" }}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocusCapture={() => setOpen(true)}
      onBlurCapture={() => setOpen(false)}
    >
      <span aria-describedby={open ? id : undefined} style={{ display: "inline-flex" }}>
        {children}
      </span>
      {open && (
        <span
          role="tooltip"
          id={id}
          style={{
            position: "absolute",
            left: "50%",
            transform: "translateX(-50%)",
            [side === "top" ? "bottom" : "top"]: "calc(100% + 7px)",
            zIndex: 60,
            width: "max-content",
            maxWidth: width,
            background: C.panel2,
            border: `1px solid ${C.line}`,
            borderRadius: 6,
            color: C.text,
            fontFamily: MONO,
            fontSize: 11,
            lineHeight: 1.5,
            padding: "7px 9px",
            textAlign: "left",
            whiteSpace: "normal",
            boxShadow: "0 8px 24px rgba(0,0,0,0.45)",
            pointerEvents: "none",
          }}
        >
          {content}
        </span>
      )}
    </span>
  );
}
