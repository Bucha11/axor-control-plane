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
import textwrap
from typing import Any


def new_token() -> str:
    """A share token: 128 bits from `secrets`, urlsafe.

    There is no registry to put it in. The `share_links` table is the only
    place a link lives — see routers.share_api.resolve_share for what the
    in-process index cost.
    """
    return secrets.token_urlsafe(16)


# Keys stripped from every export, matched case-insensitively at any depth.
#
# Two groups, and the difference matters. The first is the platform's own
# contract with its own producers: these are what a proxy detector calls a
# captured body, and they are the reason the content rule exists. The second is
# credential-shaped names, added because `observed_reality` is free-form (a
# detector, or any client with `ingest`, writes whatever it observed) and the
# other end of this is an unauthenticated public URL.
#
# It is a denylist, so it is not a proof — and it is deliberately not wider.
# Names like `text`, `payload` or `prompt` are frequently the observation
# ITSELF, and dropping them would gut the receipt the export exists to produce.
# What makes it honest is not its length: every removal is NAMED in the export
# (see `_scrub`), so a reader can tell "nothing was here" from "something was
# taken out".
_BODY_KEYS = frozenset({
    "request_body", "response_body", "raw", "body", "content",
    "authorization", "cookie", "set-cookie", "credentials", "password",
    "secret", "private_key", "api_key", "apikey", "access_token",
    "refresh_token",
})


def _scrub(value: Any, path: str = "") -> tuple[Any, list[str]]:  # noqa: ANN401
    """Strip the denied keys and report WHICH paths were stripped.

    The removal used to be invisible: a shared receipt gave its reader no way
    to tell a case that carried nothing from a case something was taken out of.
    Paths only — never the values, which is the whole point.
    """
    removed: list[str] = []
    if isinstance(value, dict):
        kept: dict[str, Any] = {}
        for key, sub in value.items():
            at = f"{path}.{key}" if path else str(key)
            if str(key).lower() in _BODY_KEYS:
                removed.append(at)
                continue
            kept[key], sub_removed = _scrub(sub, at)
            removed += sub_removed
        return kept, removed
    if isinstance(value, list):
        out = []
        for i, item in enumerate(value):
            cleaned, sub_removed = _scrub(item, f"{path}[{i}]")
            out.append(cleaned)
            removed += sub_removed
        return out, removed
    return value, removed


def evidence_receipt_html(
    run_id: str, case: dict[str, Any], scenario: str = ""
) -> str:
    """A self-contained HTML receipt — the caught discrepancy, fully legible,
    no external assets, observations only."""
    c, removed = _scrub(case)
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
    withheld = (
        "<p class=\"mono dim\">withheld by the export content rule: "
        + html.escape(", ".join(removed))
        + "</p>"
    ) if removed else ""
    return _TEMPLATE.format(
        run_id=html.escape(run_id),
        scenario=html.escape(scenario),
        deviation=html.escape(deviation.replace("_", " ").upper()),
        verdict_source=html.escape(verdict_source),
        confidence=html.escape(str(confidence)),
        observed=observed,
        claim=claim,
        fault_rows=fault_rows or "<li>—</li>",
        withheld=withheld,
    )


def _render(value: Any) -> str:  # noqa: ANN401
    import json

    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2)


# ── PDF export ────────────────────────────────────────────────────────────────
# A self-contained, dependency-free PDF (spec §8.3 wants the primary artifact to
# leave the product as a receipt, not only as HTML). Single page, the standard
# Helvetica font (no embedding needed), observations only — the same scrubbed
# content the HTML receipt carries.

# Page geometry. Text starts at the top margin and steps down by the leading;
# the last line whose baseline still clears the bottom margin is the last one
# on the page. Everything past it used to be written anyway — off the bottom of
# a MediaBox that is 792pt tall, into a PDF that is perfectly valid and simply
# does not show it.
_TOP = 760
_LEADING = 14
_BOTTOM = 56
_LINES_PER_PAGE = (_TOP - _BOTTOM) // _LEADING + 1  # 51

# The receipt embeds no font (that is what "dependency-free" costs), so the
# only glyphs it can name are the ones WinAnsiEncoding has. Anything else used
# to become "?" through `encode("latin-1", "replace")` — silently, which turned
# a Cyrillic or CJK receipt into rows of question marks and mangled the em dash
# in this module's own boilerplate. Now it is marked and counted.
_UNRENDERABLE = "[?]"


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _winansi(line: str) -> tuple[str, int]:
    """One line as WinAnsi, with the characters it could not carry counted."""
    out: list[str] = []
    dropped = 0
    for ch in line:
        try:
            ch.encode("cp1252")
        except UnicodeEncodeError:
            out.append(_UNRENDERABLE)
            dropped += 1
        else:
            out.append(ch)
    return "".join(out), dropped


def _receipt_lines(run_id: str, case: dict[str, Any], scenario: str) -> list[str]:
    c, removed = _scrub(case)
    deviation = str(c.get("deviation") or "no deviation").replace("_", " ").upper()
    lines = [
        "AXOR EVIDENCECASE",
        f"{run_id}  ·  {scenario}",
        "",
        f"VERDICT: {deviation}",
        f"source {c.get('verdict_source', '')}  ·  confidence {c.get('confidence', '')}",
        "",
        "WHAT HAPPENED (observed reality)",
    ]
    for raw in _render(c.get("observed_reality")).splitlines() or [""]:
        lines += textwrap.wrap(raw, 92) or [""]
    lines += ["", "WHAT THE AGENT SAID (claim)"]
    for raw in _render(c.get("agent_claim")).splitlines() or [""]:
        lines += textwrap.wrap(raw, 92) or [""]
    lines += ["", "FAULT ATTRIBUTION"]
    faults = c.get("fault_attribution", [])
    if faults:
        for f in faults:
            lines += textwrap.wrap(
                f"- {f.get('fault_mode')} on {f.get('tool_name')} — {f.get('influence')}",
                92,
            )
    else:
        lines.append("- —")
    lines += [
        "",
        "observations only — no raw request/response bodies are exported (spec 8.3).",
    ]
    if removed:
        lines.append("")
        for raw in textwrap.wrap(
            "withheld by the export content rule: " + ", ".join(removed), 92
        ):
            lines.append(raw)
    return lines


def _text_object(lines: list[str]) -> bytes:
    body = ["BT", "/F1 11 Tf", f"{_LEADING} TL", f"56 {_TOP} Td"]
    for line in lines:
        body.append(f"({_pdf_escape(line)}) Tj")
        body.append("T*")  # next line
    body.append("ET")
    return "\n".join(body).encode("cp1252", "replace")


def evidence_receipt_pdf(
    run_id: str, case: dict[str, Any], scenario: str = ""
) -> bytes:
    """A minimal, valid, dependency-free PDF receipt for one EvidenceCase.

    Paginated: a receipt is the artifact an operator prints and attaches, so
    content that does not fit must run onto another page, not off the bottom of
    the first one. The single-page version silently lost everything past line
    55 — on a 40-observation case that was the whole claim, the fault
    attribution, and the line stating the content rule.
    """
    lines = [line for line in _receipt_lines(run_id, case, scenario)]
    rendered: list[str] = []
    dropped = 0
    for line in lines:
        text, missing = _winansi(line)
        rendered.append(text)
        dropped += missing
    if dropped:
        rendered += ["", *textwrap.wrap(
            f"NOTE: {dropped} character(s) are shown as {_UNRENDERABLE} — this "
            f"receipt embeds no font, so it can only draw WinAnsi glyphs. The "
            f"HTML export of this case carries the text intact.", 92,
        )]

    def chunk(per_page: int) -> list[list[str]]:
        return [
            rendered[i:i + per_page]
            for i in range(0, max(len(rendered), 1), per_page)
        ] or [[]]

    pages = chunk(_LINES_PER_PAGE)
    if len(pages) > 1:
        # A "page k of n" footer only exists once there is more than one page,
        # and it needs room of its own — appended to a full page it is exactly
        # the overflow this function is here to stop.
        pages = chunk(_LINES_PER_PAGE - 2)
        n = len(pages)
        for i, page in enumerate(pages):
            page.append("")
            page.append(f"— page {i + 1} of {n} —")
    n = len(pages)

    # Object layout: 1 catalog, 2 page tree, 3 font, then one Page per page and
    # one content stream per page. Numbering is fixed so /Kids and /Contents can
    # reference forward without a second pass.
    first_page, first_stream = 4, 4 + n
    kids = " ".join(f"{first_page + i} 0 R" for i in range(n))
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids.encode("ascii"), n),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        b"/Encoding /WinAnsiEncoding >>",
    ]
    for i in range(n):
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>"
            % (first_stream + i)
        )
    for page in pages:
        content = _text_object(page)
        objects.append(
            b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content)
        )

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref_pos = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += b"%010d 00000 n \n" % off
    out += (
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF"
        % (len(objects) + 1, xref_pos)
    )
    return bytes(out)


_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8">
<title>Axor EvidenceCase — {run_id}</title>
<meta property="og:site_name" content="Axor Control Plane">
<meta property="og:type" content="article">
<meta property="og:title" content="Caught by Axor: {deviation}">
<meta property="og:description" content="A reproducible caught discrepancy \
— the agent's claim vs observed reality. Observations only; revocable link.">
<meta name="twitter:card" content="summary">
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
  {withheld}
  <p class="mono dim">Caught by
  <a href="https://axor.dev" style="color:#7FA8CC;text-decoration:none">Axor</a>
  — runtime governance for LLM agents. Catch the lie, replay it, govern the fleet.</p>
</body></html>"""
