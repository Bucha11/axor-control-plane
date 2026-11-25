"""The compliance pack, and the legal hold that keeps its evidence alive.

A reviewer asking "show me that your AI agents were governed last quarter"
wants one document, not a tour of screens. This builds it: a period — any
range, usually a quarter — of everything the plane recorded about who
oversaw which agent and why, mapped to the articles an operator is asked to
answer (``docs/eu-ai-act-mapping.md``), with a digest over the whole body so
the copy an auditor holds can be shown to be the copy that was generated.

Two rules shape it.

**It only claims what it can show.** Every section names where its rows came
from, and a section with nothing in it says so rather than being dropped — an
absent section reads as "we did not look", and the difference between "nobody
intervened" and "we have no record of interventions" is the whole point of
the document. The coverage line says plainly when the trail itself starts
later than the period asked for.

**A hold freezes the trail, retention or no retention.** A workspace sets how
long it keeps its audit trail (Business); a legal hold suspends that entirely
while it is open, because a retention policy that quietly deletes evidence
during a dispute is worse than no policy. Placing and releasing one are
themselves on the trail.

Both are the Enterprise rung's: the plan gate lives in the router, this module
only builds and stores.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from html import escape
from typing import Any

from axor_backend import audit
from axor_backend.clock import now

HOLDS_SETTING = "audit_holds"
SCHEMA = "axor.compliance/1"

# What an operator is asked to answer, and the sections of this pack that
# answer it. The mapping itself is docs/eu-ai-act-mapping.md; this is the
# machine-readable half, so the document says which of its own sections is the
# evidence for each line instead of leaving the reader to guess.
CONTROLS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("Art. 12 — Record-keeping",
     "events logged automatically over the system's lifetime, and kept",
     ("record_keeping", "oversight")),
    ("Art. 14 — Human oversight",
     "people could intervene or interrupt, and did so attributably",
     ("oversight", "attestations")),
    ("Art. 26 — Deployer obligations",
     "operation monitored, logs kept, agents suspended on risk",
     ("oversight", "fleet", "record_keeping")),
    ("Art. 15 — Accuracy and robustness",
     "policy changes tested before they took effect",
     ("policy_changes",)),
    ("Art. 79 — Market surveillance requests",
     "a recorded run can be handed over and re-derived",
     ("fleet", "record_keeping")),
    ("Access control (ISO 27001 A.5.15-18, SOC 2 CC6)",
     "who could act on the workspace, and every change to that",
     ("access_changes", "keys")),
)

# Acts that are an intervention on a running agent — Article 14's subject.
INTERVENTIONS = frozenset({
    "pause", "resume", "stop", "cascade_stop", "budget", "excise", "inject",
    "replan", "experiment", "say", "brake_on", "brake_off",
})
POLICY_ACTS = frozenset({
    "rule_allowed", "rule_refused", "rules_mode", "exception_granted",
    "exception_withdrawn", "source_trusted", "source_untrusted", "skill_off",
    "skill_on", "skill_shared_on", "skill_shared_off", "memory_on", "memory_off",
    "memory_note", "memory_fresh_days", "agent_family",
})


# ── legal hold ───────────────────────────────────────────────────────────────

async def holds(store: Any) -> list[dict[str, Any]]:  # noqa: ANN401
    held = await store.get_setting(HOLDS_SETTING)
    return [h for h in held if isinstance(h, dict)] if isinstance(held, list) else []


async def open_holds(store: Any) -> list[dict[str, Any]]:  # noqa: ANN401
    return [h for h in await holds(store) if not h.get("released_ts")]


async def place(store: Any, request: Any, reason: str, who: str) -> dict[str, Any]:  # noqa: ANN401
    """A hold, from now until someone releases it. Nothing on the trail is
    pruned while it stands."""
    hold = {"hold_id": "hold_" + secrets.token_hex(5), "reason": reason.strip()[:300],
            "placed_by": who, "placed_ts": now(), "released_ts": None, "released_by": None}
    await store.set_setting(HOLDS_SETTING, [*await holds(store), hold])
    await audit.record(store, request, "hold_placed", hold["hold_id"],
                       reason=hold["reason"])
    return hold


async def release(store: Any, request: Any, hold_id: str, who: str) -> dict[str, Any] | None:  # noqa: ANN401
    """Release one. The hold is kept, marked released: that it existed, and
    for how long, is itself part of the record."""
    held = await holds(store)
    found = next((h for h in held if h.get("hold_id") == hold_id and not h.get("released_ts")),
                 None)
    if found is None:
        return None
    found["released_ts"], found["released_by"] = now(), who
    await store.set_setting(HOLDS_SETTING, held)
    await audit.record(store, request, "hold_released", hold_id,
                       reason=str(found.get("reason") or "")[:300])
    return found


# ── the pack ─────────────────────────────────────────────────────────────────

def _count(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[str(r.get(key) or "—")] = out.get(str(r.get(key) or "—"), 0) + 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def build(*, organization: str, frm: str, to: str, generated_by: str, tier: str,
          licensed_to: str, rows: list[dict[str, Any]], attestations: list[dict[str, Any]],
          fleet: dict[str, dict[str, str]], developers: list[str], held: list[dict[str, Any]],
          retention_days: int | None, truncated_at: int = 0,
          chain: dict[str, Any] | None = None) -> dict[str, Any]:
    """One period, from rows already read for it. Pure, so the arithmetic a
    reviewer may challenge is testable without a database."""
    control_rows = [r for r in rows if r.get("kind") == audit.KIND]
    key_rows = [r for r in rows if r.get("kind") == audit.KEY_KIND]
    interventions = [r for r in control_rows if r["action"] in INTERVENTIONS]
    on_others = [r for r in interventions if _on_someone_elses(r, fleet)]
    policy = [r for r in control_rows if r["action"] in POLICY_ACTS]
    access = [r for r in control_rows if r["action"] in audit.IDENTITY_ACTS
              or r["action"].startswith("permission_")]
    kinds: dict[str, int] = {}
    for n in fleet.values():
        kinds[str(n.get("kind") or "—")] = kinds.get(str(n.get("kind") or "—"), 0) + 1
    first = min((r["ts"] for r in rows), default="")
    last = max((r["ts"] for r in rows), default="")
    body = {
        "schema": SCHEMA,
        "organization": organization,
        "licensed_to": licensed_to,
        "plan": tier,
        "period": {"from": frm, "to": to},
        "generated_on": now(),
        "generated_by": generated_by,
        "fleet": {
            "source": "the agents that reported to this plane",
            "agents_seen": len(fleet),
            "by_kind": kinds,
            "developers_running_coding_agents": len(developers),
            "developers": sorted(developers),
        },
        "oversight": {
            "source": "the workspace's audit trail of controls",
            "interventions": len(interventions),
            "by_act": _count(interventions, "action"),
            "by_person": _count(interventions, "who"),
            "on_someone_elses_agent": len(on_others),
            "on_someone_elses": [
                {"ts": r["ts"], "who": r["who"], "act": r["action"], "session": r["target"],
                 "developer": (fleet.get(r["target"]) or {}).get("developer", "")}
                for r in on_others
            ],
            "acts": [
                {"ts": r["ts"], "who": r["who"], "act": r["action"], "target": r["target"],
                 **({"detail": r["detail"]} if r.get("detail") else {})}
                for r in interventions
            ],
        },
        "attestations": {
            "source": "operator attestations on the plane (reason required, append-only)",
            "count": len(attestations),
            "acts": [
                {"ts": a.get("recorded_ts", ""), "operator": a.get("operator", ""),
                 "reason": a.get("reason", ""), "run": a.get("run_id", ""),
                 **({"revokes": a["revokes"]} if a.get("revokes") else {})}
                for a in attestations
            ],
        },
        "policy_changes": {
            "source": "the audit trail: rules, exceptions, trusted sources, skills, memory",
            "count": len(policy),
            "acts": [{"ts": r["ts"], "who": r["who"], "act": r["action"],
                      "target": r["target"], **({"detail": r["detail"]} if r.get("detail") else {})}
                     for r in policy],
        },
        "access_changes": {
            "source": "the audit trail: members, roles, sign-in, provisioning, permissions",
            "count": len(access),
            "acts": [{"ts": r["ts"], "who": r["who"], "act": r["action"], "target": r["target"],
                      **({"detail": r["detail"]} if r.get("detail") else {})} for r in access],
        },
        "keys": {
            "source": "the credential log: every key minted and revoked",
            "count": len(key_rows),
            "acts": [{"ts": r["ts"], "who": r["who"], "act": r["action"], "target": r["target"]}
                     for r in key_rows],
        },
        "record_keeping": {
            "source": "the audit trail itself",
            "acts_in_period": len(rows),
            "first_act": first,
            "last_act": last,
            "retention_days": retention_days,
            "coverage": _coverage(first, frm, retention_days, held, truncated_at),
            "legal_holds": [
                {"hold_id": h.get("hold_id"), "reason": h.get("reason"),
                 "placed_by": h.get("placed_by"), "placed_ts": h.get("placed_ts"),
                 "released_ts": h.get("released_ts"), "released_by": h.get("released_by")}
                for h in held
            ],
        },
    }
    # Per section, never summed: "61 agents" and "5 interventions" do not add
    # up to anything a reviewer could use, and a single number would invite
    # exactly that reading.
    body["controls"] = [
        {"control": name, "asks": asks,
         "evidence": {s: _section_count(body, s) for s in sections}}
        for name, asks, sections in CONTROLS
    ]
    if chain is not None:
        # the audit trail's hash chains as they stand: whole or not, and the
        # head — signed with this document, so a later trail that no longer
        # contains this head was rewritten
        body["trail_chain"] = chain
    body["integrity"] = {
        "sha256": digest(body),
        "how": "sha256 of this document with `integrity` removed, keys sorted, "
               "compact JSON, UTF-8 — recomputable from the JSON copy",
    }
    return body


def _on_someone_elses(row: dict[str, Any], fleet: dict[str, dict[str, str]]) -> bool:
    """An act on an agent whose developer is somebody else — the line a
    reviewer looks for first, because it is where oversight and privacy meet."""
    dev = str((fleet.get(row.get("target", "")) or {}).get("developer") or "")
    who = str(row.get("who") or "")
    return "@" in dev and "@" in who and dev.lower() != who.lower()


# What each section counts, said in its own units — the label a reviewer
# reads next to the number.
_SECTION_COUNT: dict[str, tuple[str, str]] = {
    "fleet": ("agents_seen", "agents"),
    "oversight": ("interventions", "interventions"),
    "attestations": ("count", "attestations"),
    "policy_changes": ("count", "policy changes"),
    "access_changes": ("count", "access changes"),
    "keys": ("count", "credential acts"),
    "record_keeping": ("acts_in_period", "acts recorded"),
}


def _section_count(body: dict[str, Any], section: str) -> int:
    key = _SECTION_COUNT.get(section, ("count", ""))[0]
    value = (body.get(section) or {}).get(key)
    return int(value) if isinstance(value, int) else 0


def section_unit(section: str) -> str:
    return _SECTION_COUNT.get(section, ("", "records"))[1]


def _coverage(first: str, frm: str, retention_days: int | None,
              held: list[dict[str, Any]], truncated_at: int = 0) -> str:
    """Said plainly, because a reviewer's first question is what is missing."""
    if truncated_at:
        return (f"this period holds more than the {truncated_at:,} acts one pack "
                f"carries, so it was cut there — ask for a shorter period, or "
                f"take the whole trail from the export")
    if not first:
        return ("no act of any kind is recorded in this period — either none "
                "happened, or this plane was not yet keeping the trail")
    if first[:10] > frm:
        kept = (f"the workspace keeps {retention_days} days" if retention_days
                else "the deployment's retention window applies")
        return (f"the trail in this period starts at {first[:10]}, after the "
                f"{frm} asked for: {kept}, so acts before that are not here")
    if any(not h.get("released_ts") for h in held):
        return "complete for the period, and a legal hold is open, so nothing is being pruned"
    return "complete for the period"


def digest(body: dict[str, Any]) -> str:
    without = {k: v for k, v in body.items() if k != "integrity"}
    return hashlib.sha256(
        json.dumps(without, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=False).encode()).hexdigest()


def signable(body: dict[str, Any]) -> bytes:
    """The bytes a signature covers: the digest, so what is signed is the
    whole document and a reviewer verifies it with one line."""
    return digest(body).encode()


# ── the readable copy ────────────────────────────────────────────────────────

_CSS = """body{font:14px/1.6 -apple-system,Segoe UI,Roboto,sans-serif;max-width:52rem;
margin:2.5rem auto;padding:0 1.5rem;color:#16181d}
h1{font-size:1.5rem;margin-bottom:.2rem}h2{font-size:1.05rem;margin:2rem 0 .4rem;
border-bottom:1px solid #e3e5ea;padding-bottom:.3rem}
.sub{color:#5b6270}table{border-collapse:collapse;width:100%;font-size:13px;margin:.5rem 0}
th,td{text-align:left;padding:.3rem .6rem;border-bottom:1px solid #eceef2;vertical-align:top}
th{color:#5b6270;font-weight:600}code{font:12px ui-monospace,Menlo,monospace;
background:#f4f5f7;padding:.1rem .3rem;border-radius:3px}
.none{color:#8a909c;font-style:italic}.note{background:#f7f8fa;border-left:3px solid #c9cdd6;
padding:.6rem .9rem;margin:.8rem 0;color:#3d434f}"""


def _when(value: str) -> str:
    """To the minute: a document read by a person, not a log."""
    return value[:16].replace("T", " ") if len(value) >= 16 else value


def _rows_table(acts: list[dict[str, Any]], columns: tuple[str, ...],
                empty: str = "nothing recorded in this period") -> str:
    if not acts:
        return f'<p class="none">{escape(empty)}</p>'
    head = "".join(f"<th>{escape(c.replace('_', ' '))}</th>" for c in columns)
    body = ""
    for a in acts[:2000]:
        cells = ""
        for c in columns:
            v = a.get(c, "")
            if isinstance(v, dict):
                v = ", ".join(f"{k}: {w}" for k, w in v.items())
            elif c.endswith(("ts", "_ts")) or c == "ts":
                v = _when(str(v or ""))
            cells += f"<td>{escape(str(v))}</td>"
        body += f"<tr>{cells}</tr>"
    more = ("" if len(acts) <= 2000 else
            f'<p class="none">{len(acts) - 2000} more in the JSON copy</p>')
    return f"<table><tr>{head}</tr>{body}</table>{more}"


def _signed_line(body: dict[str, Any]) -> str:
    integrity = body["integrity"]
    if not integrity.get("signature"):
        return ""
    return (f"<br>Signed by <code>{escape(str(integrity.get('signed_by')))}</code>: "
            f"<code>{escape(str(integrity['signature']))}</code>")


def _keeps(rk: dict[str, Any]) -> str:
    return (f"{rk['retention_days']} days" if rk["retention_days"]
            else "the deployment's window")


def as_html(body: dict[str, Any]) -> str:
    """The same pack as a document. Self-contained: no font, script or style
    fetched from anywhere, so it reads the same in an air-gapped review room
    as it does in a browser online."""
    p = body["period"]
    rk = body["record_keeping"]
    controls = "".join(
        f"<tr><td>{escape(c['control'])}</td><td>{escape(c['asks'])}</td><td>"
        + "<br>".join(f"{n} {escape(section_unit(s))}" for s, n in c["evidence"].items())
        + "</td></tr>"
        for c in body["controls"])
    holds_html = _rows_table(
        rk["legal_holds"], ("hold_id", "reason", "placed_by", "placed_ts", "released_ts"),
        empty="no hold has been placed — the trail is kept under the retention above")
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Governed-agent compliance pack — {escape(body['organization'])}
— {escape(p['from'])} to {escape(p['to'])}</title>
<style>{_CSS}</style></head><body>
<h1>Governed-agent compliance pack</h1>
<p class="sub">{escape(body['organization'])} · {escape(p['from'])} to {escape(p['to'])}
· generated {escape(body['generated_on'][:16].replace('T', ' '))}
by {escape(body['generated_by'])} · plan {escape(body['plan'])}</p>
<div class="note">This document is drawn from what this control plane recorded.
Each section names its source, and a section with nothing in it says so — an
empty section means nothing was recorded, not that nothing was looked for.
<br>Integrity: <code>{escape(body['integrity']['sha256'])}</code>
— {escape(body['integrity']['how'])}.
{_signed_line(body)}
</div>

<h2>What was governed</h2>
<p>{body['fleet']['agents_seen']} agents reported to this plane
({escape(', '.join(f"{k}: {v}" for k, v in body['fleet']['by_kind'].items()) or '—')}),
run by {body['fleet']['developers_running_coding_agents']} developers.</p>

<h2>Human oversight — who intervened, and how</h2>
<p>{body['oversight']['interventions']} interventions by
{len(body['oversight']['by_person'])} people;
{body['oversight']['on_someone_elses_agent']} on someone else's agent.</p>
{_rows_table(body['oversight']['acts'], ("ts", "who", "act", "target", "detail"),
             empty="no agent was paused, stopped, capped or braked in this period")}

<h2>Attestations — a reason on the record</h2>
{_rows_table(body['attestations']['acts'], ("ts", "operator", "reason", "run"))}

<h2>Policy changes</h2>
{_rows_table(body['policy_changes']['acts'], ("ts", "who", "act", "target", "detail"))}

<h2>Access — who could act, and every change to it</h2>
{_rows_table(body['access_changes']['acts'], ("ts", "who", "act", "target", "detail"))}

<h2>Credentials</h2>
{_rows_table(body['keys']['acts'], ("ts", "who", "act", "target"))}

<h2>Record-keeping</h2>
<p>{rk['acts_in_period']} acts recorded,
{escape(rk['first_act'][:16].replace('T', ' ') or '—')}
to {escape(rk['last_act'][:16].replace('T', ' ') or '—')}. Retention: {_keeps(rk)}.</p>
<div class="note">{escape(rk['coverage'])}</div>
<h3>Legal holds</h3>
{holds_html}

<h2>What this answers</h2>
<table><tr><th>Control</th><th>Asks for</th><th>Evidence in this pack</th></tr>
{controls}</table>
<p class="sub">The mapping is this product's reading of the obligations, not
legal advice; <code>docs/eu-ai-act-mapping.md</code> explains each line.</p>
</body></html>"""
