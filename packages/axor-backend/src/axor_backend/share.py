"""EvidenceCase export & share (spec section 8.3).

The primary artifact must be able to leave the product, or it isn't primary.

- Export: a single EvidenceCase -> a self-contained HTML receipt (observed
  reality | claim | verdict | trace excerpt | replay reference).
- Share: a revocable permalink with a scoped read token — one case, no
  navigation to the rest of the workspace.
- Content rule: exports carry observations, labels, and verdicts — never raw
  request/response bodies. If it wasn't stored, it can't leak via export.
"""
from __future__ import annotations

import html
import secrets
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ShareLink:
    token: str
    run_id: str
    case_index: int
    revoked: bool = False


@dataclass
class ShareRegistry:
    _links: dict[str, ShareLink] = field(default_factory=dict)

    def create(self, run_id: str, case_index: int) -> ShareLink:
        token = secrets.token_urlsafe(16)
        link = ShareLink(token=token, run_id=run_id, case_index=case_index)
        self._links[token] = link
        return link

    def resolve(self, token: str) -> ShareLink | None:
        link = self._links.get(token)
        if link is None or link.revoked:
            return None
        return link

    def revoke(self, token: str) -> bool:
        link = self._links.get(token)
        if link is None:
            return False
        link.revoked = True
        return True


# Fields that may NEVER appear in an export even if some upstream stored them.
_BODY_KEYS = frozenset({"request_body", "response_body", "raw", "body", "content"})


def _scrub(value: Any) -> Any:  # noqa: ANN401 - arbitrary EvidenceCase JSON
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items() if k not in _BODY_KEYS}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def evidence_receipt_html(
    run_id: str, case: dict[str, Any], scenario: str = ""
) -> str:
    """A self-contained HTML receipt — the caught discrepancy, fully legible,
    no external assets, observations only."""
    c = _scrub(case)
    deviation = str(c.get("deviation") or "no deviation")
    verdict_source = str(c.get("verdict_source", ""))
    confidence = c.get("confidence", "")
    observed = html.escape(_render(c.get("observed_reality")))
    claim = html.escape(_render(c.get("agent_claim")))
    faults = c.get("fault_attribution", [])
    fault_rows = "".join(
        f"<li>{html.escape(str(f.get('fault_mode')))} on "
        f"{html.escape(str(f.get('tool_name')))} — {html.escape(str(f.get('influence')))}</li>"
        for f in faults
    )
    return _TEMPLATE.format(
        run_id=html.escape(run_id),
        scenario=html.escape(scenario),
        deviation=html.escape(deviation.replace("_", " ").upper()),
        verdict_source=html.escape(verdict_source),
        confidence=html.escape(str(confidence)),
        observed=observed,
        claim=claim,
        fault_rows=fault_rows or "<li>—</li>",
    )


def _render(value: Any) -> str:  # noqa: ANN401
    import json

    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2)


_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8">
<title>Axor EvidenceCase — {run_id}</title>
<style>
  body {{ background:#12161A; color:#D2DAE1; font-family:system-ui,sans-serif;
          max-width:640px; margin:40px auto; padding:0 20px; }}
  .mono {{ font-family:ui-monospace,Menlo,monospace; }}
  .mut {{ color:#78848F; }} .dim {{ color:#4C5760; font-size:11px; letter-spacing:.1em; }}
  .panel {{ background:#191F26; border:1px solid #262E37; border-radius:8px;
            overflow:hidden; margin-top:20px; }}
  .row {{ padding:16px; border-bottom:1px solid #262E37; }}
  .verdict {{ padding:12px 16px; background:rgba(229,72,77,0.06); color:#E5484D;
              font-weight:700; }}
  h1 {{ font-size:20px; font-weight:650; }}
  ul {{ padding-left:18px; }}
</style></head>
<body>
  <div class="mono mut">{run_id} · {scenario}</div>
  <h1>Axor EvidenceCase</h1>
  <div class="panel">
    <div class="row"><div class="dim mono">WHAT HAPPENED</div>
      <pre class="mono">{observed}</pre></div>
    <div class="row"><div class="dim mono">WHAT THE AGENT SAID</div>
      <pre class="mono">{claim}</pre></div>
    <div class="verdict mono">{deviation} · {verdict_source} · confidence {confidence}</div>
  </div>
  <p class="mono mut">fault attribution</p>
  <ul class="mono mut">{fault_rows}</ul>
  <p class="mono dim">observations only — no raw request/response bodies are
  exported (spec section 8.3).</p>
</body></html>"""
