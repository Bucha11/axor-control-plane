// One screen that throws while rendering must not take the whole app with it:
// a URIError in one route blanked every page until a reload (QA-043). The
// guard is keyed by the route, so moving to another page starts it afresh.
import { Component, type ReactNode } from "react";
import { C, MONO, btn } from "../theme";

interface State { error: Error | null }

export default class PageGuard extends Component<{ children: ReactNode }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error): void {
    console.error("this page failed to render", error);
  }

  render(): ReactNode {
    if (!this.state.error) return this.props.children;
    return (
      <div data-testid="page-failed" style={{ maxWidth: 640, margin: "48px auto", fontFamily: MONO, fontSize: 12, color: C.text, lineHeight: 1.7 }}>
        <div style={{ color: C.amber }}>This page could not be shown.</div>
        <div style={{ color: C.mut }}>
          The link may be cut short or mistyped. The rest of the app works — pick another page,
          or go home.
        </div>
        <button onClick={() => { window.location.hash = "#/"; }} className="mt-2"
          style={btn({ color: C.text, borderColor: C.steel, fontSize: 11.5, padding: "4px 10px" })}>
          go home
        </button>
      </div>
    );
  }
}
