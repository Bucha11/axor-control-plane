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


# What a case is, as one sentence a reader outside the team understands.
HEADLINE: dict[str, str] = {
    "claimed_checks_passed": "The agent said the checks passed. They failed.",
    "fabricated_tool_result": "The agent said a tool worked. It had failed.",
    "tainted_value_exfiltrated": "The agent tried to send out text that came from outside.",
    "corrupted_retrieval_used": "The agent relied on a poisoned search result.",
    "direct_policy_violation": "The agent followed an instruction hidden in outside text.",
    "undisclosed_tool_substitution": "A tool was swapped, and the agent didn't say so.",
    "budget_misreport": "The agent misreported its budget.",
    "fabrication_contained": "A made-up result was stopped before it spread.",
    "behavioral_drift": "The agent's behaviour drifted from what it was checked to do.",
}
TOOL_DID: dict[str, str] = {
    "silent_fail": "returned an error",
    "corrupt_retrieval": "returned corrupted results",
    "instruction_injection": "returned text with a hidden instruction in it",
    "tool_substitution": "was swapped for a different tool",
    "timeout": "timed out",
    "empty_result": "returned nothing",
}
_KEY_WORDS: dict[str, str] = {
    "driving_value": "what it carried", "provenance": "where that came from",
    "actual_result": "what the tool did", "injected": "broken on purpose",
    "tool_name": "tool",
}
_EXAMPLE_SCENARIOS = frozenset({"adapter-demo", "multi-agent-demo", "coding-demo", "skill-demo",
                                "ops-demo"})


def headline(deviation: str) -> str:
    return HEADLINE.get(deviation) or (deviation.replace("_", " ").capitalize() + "."
                                       if deviation else "Something did not add up.")


def how_we_know(source: str, confidence: object) -> str:
    if source == "deterministic":
        return "certain — read from what was recorded, not a guess"
    try:
        pct = f" ({round(float(confidence) * 100)}% sure)"  # type: ignore[arg-type]
    except (TypeError, ValueError):
        pct = ""
    return f"likely — the agent's words read against what was recorded{pct}"


def _observed_rows(value: Any) -> list[tuple[str, str]]:  # noqa: ANN401
    """What happened, as (label, text) rows — a check as one sentence."""
    if isinstance(value, dict):
        if isinstance(value.get("check"), str):
            verdict = "passed" if value.get("passed") else "failed"
            text = f"{verdict}" + (f" — {value['summary']}" if value.get("summary") else "")
            return [(str(value["check"]), text),
                    *([("command", str(value["command"]))] if value.get("command") else [])]
        return [(_KEY_WORDS.get(str(k), str(k).replace("_", " ")),
                 v if isinstance(v, str) else _render(v)) for k, v in value.items()]
    return [("", _render(value))]


def evidence_receipt_html(
    run_id: str, case: dict[str, Any], scenario: str = "", *,
    node_id: str = "", ts: str = "",
) -> str:
    """A self-contained HTML receipt — the caught discrepancy, legible to
    someone outside the team, on a phone too; no external assets,
    observations only."""
    c, removed = _scrub(case)
    deviation = str(c.get("deviation") or "")
    title = headline(deviation)
    rows = "".join(
        f'<div class="kv"><span class="k">{html.escape(k)}</span>'
        f'<span class="v">{html.escape(v)}</span></div>' if k else
        f'<div class="v">{html.escape(v)}</div>'
        for k, v in _observed_rows(c.get("observed_reality")))
    claim = c.get("agent_claim")
    claim_text = claim if isinstance(claim, str) else _render(claim)
    faults = c.get("fault_attribution") or []
    def did(mode: object) -> str:
        return TOOL_DID.get(str(mode), str(mode).replace("_", " "))

    why = "".join(
        f"<li><b>{html.escape(str(f.get('tool_name')))}</b> {html.escape(did(f.get('fault_mode')))}"
        f"{' — it shaped what the agent did' if f.get('influence') == 'strong' else ''}</li>"
        for f in faults if isinstance(f, dict))
    why_block = f'<div class="label">WHY</div><ul>{why}</ul>' if why else ""
    withheld = (
        '<p class="small">left out of this link (it could carry contents): '
        + html.escape(", ".join(removed)) + "</p>"
    ) if removed else ""
    when = html.escape(ts[:16].replace("T", " ")) if ts else ""
    meta = " · ".join(x for x in (html.escape(node_id), when) if x)
    example = ('<span class="tag">example data</span>'
               if scenario in _EXAMPLE_SCENARIOS else "")
    return _TEMPLATE.format(
        title=html.escape(title), meta=meta, example=example, rows=rows,
        claim=html.escape(claim_text), how=html.escape(
            how_we_know(str(c.get("verdict_source", "")), c.get("confidence"))),
        why=why_block, withheld=withheld, ref=html.escape(run_id),
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
        "AXOR — CAUGHT",
        headline(str(c.get("deviation") or "")),
        f"ref {run_id}  ·  {scenario}",
        "",
        f"VERDICT: {deviation}",
        "How we know: " + how_we_know(str(c.get("verdict_source", "")), c.get("confidence")),
        "",
        "WHAT ACTUALLY HAPPENED",
    ]
    for raw in _render(c.get("observed_reality")).splitlines() or [""]:
        lines += textwrap.wrap(raw, 92) or [""]
    lines += ["", "WHAT THE AGENT SAID"]
    for raw in _render(c.get("agent_claim")).splitlines() or [""]:
        lines += textwrap.wrap(raw, 92) or [""]
    lines += ["", "WHY"]
    faults = c.get("fault_attribution", [])
    if faults:
        for f in faults:
            mode = str(f.get("fault_mode"))
            lines += textwrap.wrap(
                f"- {f.get('tool_name')} {TOOL_DID.get(mode, mode.replace('_', ' '))}"
                f" ({f.get('influence')} influence)", 92)
    else:
        lines.append("- —")
    lines += [
        "",
        "Only what was observed — no request or response contents.",
    ]
    if removed:
        lines.append("")
        for raw in textwrap.wrap(
            "left out (it could carry contents): " + ", ".join(removed), 92
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
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — caught by Axor</title>
<meta property="og:site_name" content="Axor">
<meta property="og:type" content="article">
<meta property="og:title" content="{title}">
<meta property="og:description" content="What an AI agent said, against what actually \
happened — caught by Axor. Observations only.">
<meta name="twitter:card" content="summary">
<style>
  * {{ box-sizing:border-box; }}
  body {{ background:#12161A; color:#D2DAE1; font-family:system-ui,-apple-system,sans-serif;
          max-width:640px; margin:0 auto; padding:32px 18px 48px; line-height:1.5; }}
  .mono, pre, .v, .k {{ font-family:ui-monospace,Menlo,monospace; }}
  .top {{ display:flex; justify-content:space-between; align-items:center; gap:8px;
          font-size:12px; color:#78848F; }}
  .brand {{ font-family:ui-monospace,Menlo,monospace; font-weight:700; color:#D2DAE1;
            letter-spacing:.04em; }}
  .tag {{ border:1px solid #F2A33C; color:#F2A33C; border-radius:10px; padding:1px 8px;
          font-size:11px; }}
  h1 {{ font-size:24px; line-height:1.3; font-weight:700; margin:22px 0 6px; color:#F0F3F6; }}
  .meta {{ font-size:12.5px; color:#78848F; font-family:ui-monospace,Menlo,monospace;
           overflow-wrap:anywhere; }}
  .panel {{ background:#191F26; border:1px solid #262E37; border-radius:10px;
            overflow:hidden; margin-top:22px; }}
  .row {{ padding:16px; border-bottom:1px solid #262E37; }}
  .label {{ font-family:ui-monospace,Menlo,monospace; font-size:11px; letter-spacing:.1em;
            color:#78848F; margin-bottom:8px; }}
  .kv {{ display:flex; gap:12px; font-size:13.5px; padding:2px 0; flex-wrap:wrap; }}
  .k {{ color:#78848F; min-width:150px; }}
  .v {{ color:#D2DAE1; white-space:pre-wrap; overflow-wrap:anywhere; flex:1; min-width:0;
        font-size:13.5px; }}
  .said {{ font-size:16px; color:#F0F3F6; }}
  .how {{ padding:12px 16px; background:rgba(229,72,77,0.07); color:#E5484D; font-size:13px; }}
  ul {{ padding-left:18px; margin:0; font-size:13.5px; color:#B8C2CC; }}
  .why {{ margin-top:22px; }}
  .small {{ font-size:12px; color:#5F6B76; }}
  a {{ color:#7FA8CC; text-decoration:none; }}
  footer {{ margin-top:28px; font-size:12.5px; color:#78848F; border-top:1px solid #262E37;
            padding-top:14px; }}
</style></head>
<body>
  <div class="top"><span class="brand">AXOR</span>{example}</div>
  <h1>{title}</h1>
  <div class="meta">{meta}</div>
  <div class="panel">
    <div class="row"><div class="label">WHAT ACTUALLY HAPPENED</div>{rows}</div>
    <div class="row"><div class="label">WHAT THE AGENT SAID</div>
      <div class="said">“{claim}”</div></div>
    <div class="how">How we know: {how}</div>
  </div>
  <div class="why">{why}</div>
  {withheld}
  <footer>
    This link shows only what was observed — no request or response contents. Whoever shared
    it can withdraw it. <span class="small mono">ref {ref}</span><br><br>
    Caught by <a href="https://axor.dev">Axor</a> — it checks what AI agents do against what
    they say, and stops the dangerous part.
  </footer>
</body></html>"""
