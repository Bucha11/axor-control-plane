"""Coding agents, governed from the plane: skills turned off — and on — for
the team.

A skill (``.claude/skills/<name>`` / ``.codex/skills/<name>``) is turned off
here for every developer of the organization. The plane cannot touch a
developer's disk; what it can do is tell every axor sidecar, which refuses the
agent's use of it — the ``Skill`` call naming it, and any read, run or edit of
a file under its folder. The skill's name and description stay in the agent's
context (the agent loads them at start) until the folder is removed; the
screen says so.

- ``GET  /v1/coding/policy``       what the sidecars fetch (an ingest key reads it)
- ``GET  /v1/coding/rules``        the team's rules for the screen, with the catalog
- ``PUT  /v1/coding/rules``        ``{"allow": [...], "mode": "enforce"|"observe"}`` — admin
- ``GET  /v1/coding/skills``       the list for the screen, with recent refusals
- ``PUT  /v1/coding/skills/{name}`` ``{"off": true|false}`` or
  ``{"team": true|false}`` — operate
- ``POST /v1/coding/skill-bundles``  a skill's files, shared for the team to
  turn on (``axor-wrap skill share``; an ingest key)
- ``GET  /v1/coding/skill-bundles/{name}`` the version turned on, files and
  all — what a sidecar installs into the agent's skills folder

A shared skill reaches nobody until someone turns it on, and a new upload of
it reaches nobody until it is turned on again: the version on for the team is
kept apart from the latest upload. Turning a skill off for the team also takes
it off the team, so the sidecars remove what they installed.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from axor_backend.deps import PrincipalDep, StoreDep
from axor_backend.tenancy import current_org_id

router = APIRouter(prefix="/v1/coding", tags=["coding"])

SKILLS_OFF = "coding_skills_off"
_NAME = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
# the reason the sidecar gives (axor_wrap.agents.rules.skill_refusal)
_REFUSED = re.compile(r"the skill '([^']+)' is turned off from the Control Plane")


async def _off(request: Request) -> dict[str, dict]:
    value = await request.app.state.store.get_setting(SKILLS_OFF)
    return value if isinstance(value, dict) else {}


@router.get("/policy")
async def policy(request: Request, store: StoreDep) -> dict:
    """For the sidecars: the skills off and on, and — once an admin saved one —
    the team's rules: which the hooks refuse, which they allow, and whether
    they refuse at all (``enforce``) or only record what they would have."""
    team = [{"name": t["name"], "version": t["live"]["version"]}
            for t in await store.team_skills() if t["live"]]
    rules = await _rules(store)
    signed = await _signed(store)
    return {"skills_off": sorted(await _off(request)), "skills_team": team,
            # what widens, as an operator last signed it (sidecars with
            # operator keys take rules and team skills only from here)
            **({"signed": {k: signed[k] for k in ("payload", "operator", "sig")}}
               if signed else {}),
            # browser keys machines may add (endorsed by one they trust) and
            # the ones revoked (dropping a key only narrows)
            **({"operators": ops} if (ops := await _operators_for_machines(store))["endorsed"]
               or ops["revoked"] else {}),
            **({"rules": {k: rules[k] for k in ("allow", "mode", "exceptions", "trusted_sources")}}
               if rules["set"] else {}),
            # an admin's brake: every coding agent of the team held at once
            **({"brake": brake} if (brake := await _brake(store)) else {})}


async def _operators_for_machines(store: Any) -> dict:  # noqa: ANN401
    from axor_backend import operators

    return await operators.for_machines(store)


# ── the team's rules for coding agents ───────────────────────────────────────
# What the hooks refuse whoever asked (axor_wrap.agents.rules): the same list
# for every developer, decided once here. A repository's .axor/rules.json may
# refuse more ("refuse": [...]); it may not allow what the team refuses.

RULES = "coding_rules"
MODES = ("enforce", "observe")
# the catalog, when the backend's axor-wrap cannot say it (an older one)
_CATALOG = [
    ("download_and_run", "downloads code and runs it",
     "what it downloads is never seen before it runs"),
    ("secrets", "reads secrets",
     "keys and tokens in the agent's context can be sent anywhere it can reach"),
    ("sends_out", "sends a file or data out of this machine",
     "uploads and raw network pipes are how data leaves"),
    ("persistence", "changes what runs later",
     "shell profiles, git hooks, cron and services outlive the session"),
    ("writes_outside", "writes outside the repository",
     "a coding task changes this repository, not the rest of the machine"),
    ("destructive", "deletes or rewrites what cannot be undone",
     "a recursive delete outside the repository, a force push, a disk write"),
    ("privilege", "asks for root",
     "a coding task does not need to become root"),
    ("skill_scripts", "runs a skill's script that does something dangerous",
     "a script is one call to Axor — what it does inside is not seen line by line"),
]
# the agent's own guard is never the team's to switch off
_FIXED = {"agent_config"}


def _catalog() -> list[dict]:
    try:
        from axor_wrap.agents.rules import RULES as WRAP_RULES

        rows = [(r.id, r.what, r.why) for r in WRAP_RULES.values()]
    except Exception:  # noqa: BLE001 - an older axor-wrap: the list above
        rows = list(_CATALOG)
    return [{"id": i, "what": w, "why": y, "fixed": i in _FIXED} for i, w, y in rows]


async def _rules(store: object) -> dict:
    value = await store.get_setting(RULES)  # type: ignore[attr-defined]
    if not isinstance(value, dict):
        return {"set": False, "allow": [], "mode": "enforce", "exceptions": [],
                "trusted_sources": [], "by": "", "ts": ""}
    return {"set": True, "allow": sorted(value.get("allow") or []),
            "mode": value.get("mode") if value.get("mode") in MODES else "enforce",
            "exceptions": list(value.get("exceptions") or []),
            "trusted_sources": list(value.get("trusted_sources") or []),
            "by": value.get("by", ""), "ts": value.get("ts", "")}


MAX_EXCEPTIONS, MAX_SOURCES = 100, 30
_HOST = re.compile(r"^(?:\*\.)?(?:[a-z0-9-]+\.)+[a-z]{2,}$")


def _exceptions(raw: object, known: set[str]) -> list[dict]:
    """An exception lets ONE thing a rule refuses through, for the team: a path
    (``tests/fixtures/*``) or a command (``env | grep JWT``), * as a wildcard.
    Never a whole rule (that is ``allow``), never the agent's own guard."""
    if not isinstance(raw, list) or len(raw) > MAX_EXCEPTIONS:
        raise HTTPException(400, f"exceptions is a list of at most {MAX_EXCEPTIONS}")
    out = []
    for e in raw:
        if not isinstance(e, dict) or e.get("rule") not in known or e.get("rule") in _FIXED:
            raise HTTPException(
                400, "an exception names a rule from the catalog (not agent_config)")
        kind = "path" if e.get("path") else "command" if e.get("command") else None
        value = str(e.get(kind) or "").strip() if kind else ""
        if not kind or not value or len(value) > 300 or value.strip("*/ ") == "":
            raise HTTPException(400, "an exception is a path or a command — not everything (*)")
        out.append({"rule": e["rule"], kind: value, "note": str(e.get("note") or "")[:200],
                    "by": str(e.get("by") or ""), "ts": str(e.get("ts") or "")})
    return out


def _sources(raw: object) -> list[str]:
    """Hosts whose text is not "from outside": a page there is read like the
    developer's own words, so a command it shows may be run. Exact hosts, or
    *.example.com for its subdomains — never a bare TLD or everything."""
    if not isinstance(raw, list) or len(raw) > MAX_SOURCES:
        raise HTTPException(400, f"trusted_sources is a list of at most {MAX_SOURCES} hosts")
    hosts = sorted({str(h).strip().lower() for h in raw if str(h).strip()})
    bad = [h for h in hosts if not _HOST.match(h)]
    if bad:
        raise HTTPException(400, f"not a host: {', '.join(bad)} (e.g. pypi.org, *.readthedocs.io)")
    return hosts


@router.get("/rules")
async def rules_for_screen(store: StoreDep) -> dict:
    return {**await _rules(store), "catalog": _catalog(),
            "signed": await signing_state(store)}


@router.put("/rules")
async def set_rules(body: dict, request: Request, store: StoreDep,
                    principal: PrincipalDep) -> dict:
    """The team's rules: ``{"allow": [rule ids], "mode": "enforce"|"observe"}``.
    An owner or admin: it decides what every developer's agent may do."""
    from axor_backend import permissions

    permissions.require_admin(principal)
    known = {r["id"] for r in _catalog()}
    allow = body.get("allow")
    if not isinstance(allow, list) or not all(isinstance(x, str) and x in known for x in allow):
        raise HTTPException(400, f"allow is a list of rule ids, from {sorted(known)}")
    if any(x in _FIXED for x in allow):
        raise HTTPException(400, "the agent's own guard (agent_config) cannot be allowed")
    mode = body.get("mode", "enforce")
    if mode not in MODES:
        raise HTTPException(400, f"mode is one of {list(MODES)}")
    held = await _rules(store)
    now_ts = datetime.now(UTC).isoformat()
    # left out of the body: kept as they were (the screen saves rules and mode)
    exceptions = (_exceptions(body["exceptions"], known) if "exceptions" in body
                  else held["exceptions"])
    for e in exceptions:
        e["by"] = e["by"] or _by(principal)
        e["ts"] = e["ts"] or now_ts
    sources = (_sources(body["trusted_sources"]) if "trusted_sources" in body
               else held["trusted_sources"])
    await store.set_setting(RULES, {"allow": sorted(set(allow)), "mode": mode,
                                    "exceptions": exceptions, "trusted_sources": sources,
                                    "by": _by(principal), "ts": now_ts})
    await _audit_rules(store, request, held, sorted(set(allow)), mode, exceptions, sources)
    return await _rules(store)


async def _permitted(store: Any, principal: object, cap: str) -> None:  # noqa: ANN401
    from axor_backend import permissions

    if not await permissions.may(store, principal, cap):
        raise HTTPException(403, {"code": "not_permitted", "capability": cap,
                                  "detail": f"this workspace lets only some people "
                                            f"{permissions.CAPABILITIES[cap][0]} — ask an admin"})


@router.put("/exceptions")
async def set_exceptions(body: dict, request: Request, store: StoreDep,
                         principal: PrincipalDep) -> dict:
    """The team's exceptions and trusted sources alone, without its rules —
    for whoever the workspace lets grant them (permissions: exceptions), not
    only the admin who sets the rules. Either list may be left out."""
    await _permitted(store, principal, "exceptions")
    known = {r["id"] for r in _catalog()}
    held = await _rules(store)
    now_ts = datetime.now(UTC).isoformat()
    exceptions = (_exceptions(body["exceptions"], known) if "exceptions" in body
                  else held["exceptions"])
    for e in exceptions:
        e["by"] = e["by"] or _by(principal)
        e["ts"] = e["ts"] or now_ts
    sources = (_sources(body["trusted_sources"]) if "trusted_sources" in body
               else held["trusted_sources"])
    await store.set_setting(RULES, {"allow": held["allow"], "mode": held["mode"],
                                    "exceptions": exceptions, "trusted_sources": sources,
                                    "by": _by(principal), "ts": now_ts})
    await _audit_rules(store, request, held, held["allow"], held["mode"], exceptions, sources)
    return await _rules(store)


def _exception_words(e: dict) -> str:
    return f"{e['rule']}: {e.get('path') or e.get('command')}"


async def _audit_rules(store: Any, request: Request, held: dict, allow: list[str],  # noqa: ANN401
                       mode: str, exceptions: list[dict], sources: list[str]) -> None:
    """What the save changed, act by act: a rule allowed or refused again, the
    mode, an exception granted or withdrawn, a source trusted or not."""
    from axor_backend import audit

    was_allow = set(held.get("allow") or [])
    for rule in sorted(set(allow) - was_allow):
        await audit.record(store, request, "rule_allowed", rule)
    for rule in sorted(was_allow - set(allow)):
        await audit.record(store, request, "rule_refused", rule)
    if mode != held.get("mode", "enforce"):
        await audit.record(store, request, "rules_mode", mode)
    old = {_exception_words(e) for e in held.get("exceptions") or []}
    new = {_exception_words(e): e for e in exceptions}
    for words in sorted(set(new) - old):
        note = new[words].get("note") or ""
        await audit.record(store, request, "exception_granted", words,
                           **({"note": audit.first_words(note)} if note else {}))
    for words in sorted(old - set(new)):
        await audit.record(store, request, "exception_withdrawn", words)
    was = set(held.get("trusted_sources") or [])
    for host in sorted(set(sources) - was):
        await audit.record(store, request, "source_trusted", host)
    for host in sorted(was - set(sources)):
        await audit.record(store, request, "source_untrusted", host)


@router.get("/skills")
async def skills(request: Request) -> dict:
    off = await _off(request)
    attempts: Counter[str] = Counter()
    for row in await request.app.state.store.recent_refusals():
        m = _REFUSED.search(str(row["payload"].get("reason", "")))
        if m:
            attempts[m.group(1)] += 1
    return {"off": [{"skill": k, **v} for k, v in sorted(off.items())],
            "attempts": dict(attempts),
            "team": [_team_row(t) for t in await request.app.state.store.team_skills()]}


def _team_row(t: dict) -> dict:
    latest, live = t["latest"], t["live"]
    skill_md = next((f["content"] for f in latest["files"] if f["path"].lower() == "skill.md"), "")
    return {"name": t["name"], "description": latest.get("description", ""),
            "latest": {k: latest.get(k) for k in ("version", "by", "ts", "check")}
            | {"files": [f["path"] for f in latest["files"]]},
            "live": {k: live.get(k) for k in ("version", "by", "ts")} if live else None,
            "skill_md": skill_md[:4000]}


def _by(principal: object) -> str:
    if principal is None:
        return ""
    return str(getattr(principal, "email", "") or getattr(principal, "key_id", "") or "")


@router.put("/skills/{name}")
async def set_skill(name: str, body: dict, request: Request, principal: PrincipalDep,
                    store: StoreDep) -> dict:
    if not _NAME.match(name):
        raise HTTPException(400, "a skill name is a plain id (letters, digits, - _ . :)")
    await _permitted(store, principal, "skills")
    if isinstance(body.get("team"), bool):
        return await _set_team(name, body["team"], request, principal, store)
    if not isinstance(body.get("off"), bool):
        raise HTTPException(400, 'body is {"off": true|false} or {"team": true|false}')
    off = await _off(request)
    if body["off"]:
        off[name] = {"ts": datetime.now(UTC).isoformat(), "by": _by(principal)}
        shared = await store.team_skill(name)
        if shared and shared["live"]:
            # off for the team is off the team: the sidecars remove their copy
            await store.put_team_skill(name, shared["latest"], None)
    else:
        off.pop(name, None)
    await request.app.state.store.set_setting(SKILLS_OFF, off)
    from axor_backend import audit

    await audit.record(store, request, "skill_off" if body["off"] else "skill_on", name)
    return {"skills_off": sorted(off)}


async def _set_team(name: str, on: bool, request: Request, principal: object,
                    store: StoreDep) -> dict:
    shared = await store.team_skill(name)
    if shared is None:
        raise HTTPException(404, f"no skill '{name}' was shared — `axor-wrap skill share "
                                 "<folder>` uploads one")
    if on and name in await _off(request):
        raise HTTPException(409, f"'{name}' is turned off for the team — turn it back on "
                                 "before sharing it")
    live = None
    if on:
        latest = shared["latest"]
        live = {"version": latest["version"], "files": latest["files"],
                "by": _by(principal), "ts": datetime.now(UTC).isoformat()}
    await store.put_team_skill(name, shared["latest"], live)
    from axor_backend import audit

    await audit.record(store, request, "skill_shared_on" if on else "skill_shared_off", name,
                       **({"version": live["version"]} if live else {}))
    return {"name": name, "team": on, "version": live["version"] if live else None}


# ── skills shared for the team ───────────────────────────────────────────────

_FOLDER_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")
MAX_FILES = 64
MAX_BYTES = 512_000


def _bundle_files(body: dict) -> list[dict]:
    files = body.get("files")
    if not isinstance(files, list) or not files or len(files) > MAX_FILES:
        raise HTTPException(400, f"files is a list of 1..{MAX_FILES} {{path, content}}")
    out, total = [], 0
    for f in files:
        path = str(f.get("path", "")).replace("\\", "/") if isinstance(f, dict) else ""
        content = f.get("content") if isinstance(f, dict) else None
        parts = path.split("/")
        if (not path or path.startswith("/") or not isinstance(content, str)
                or any(p in ("", ".", "..") or p.startswith(".") for p in parts)):
            raise HTTPException(400, f"a file's path stays inside the skill's folder: {path!r}")
        total += len(content)
        out.append({"path": path, "content": content})
    if total > MAX_BYTES:
        raise HTTPException(413, f"a shared skill is at most {MAX_BYTES} bytes of text")
    if not any(f["path"].lower() == "skill.md" for f in out):
        raise HTTPException(400, "a skill has its SKILL.md at the top of its folder")
    return sorted(out, key=lambda f: f["path"])


def _static_check(files: list[dict]) -> dict | None:
    try:
        from axor_wrap.agents.skills import check
    except ImportError:
        return None
    report = check([(f["path"], f["content"]) for f in files]).as_dict()
    return {"summary": report["summary"], "description": report.get("description", ""),
            "findings": report["findings"][:30]}


@router.post("/skill-bundles", status_code=201)
async def share_skill(body: dict, store: StoreDep, principal: PrincipalDep) -> dict:
    name = str(body.get("name", ""))
    if not _FOLDER_NAME.match(name):
        raise HTTPException(400, "a skill's name is its folder name (letters, digits, - _ .)")
    files = _bundle_files(body)
    version = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()[:12]
    check = _static_check(files)
    latest = {"version": version, "files": files, "by": _by(principal),
              "ts": datetime.now(UTC).isoformat(), "check": check,
              "description": (check or {}).get("description", "")}
    shared = await store.team_skill(name)
    live = shared["live"] if shared else None
    await store.put_team_skill(name, latest, live)
    return {"name": name, "version": version, "live_version": live["version"] if live else None,
            "check": check["summary"] if check else None}


@router.get("/skill-bundles/{name}")
async def team_bundle(name: str, store: StoreDep) -> dict:
    """The version on for the team — what a sidecar installs."""
    shared = await store.team_skill(name)
    if shared is None or not shared["live"]:
        raise HTTPException(404, f"'{name}' is not on for the team")
    return {"name": name, "version": shared["live"]["version"], "files": shared["live"]["files"]}


# ── the team's brake ─────────────────────────────────────────────────────────

BRAKE = "coding_brake"


async def _brake(store: Any) -> dict | None:  # noqa: ANN401
    value = await store.get_setting(BRAKE)
    return value if isinstance(value, dict) and value.get("on") else None


@router.get("/brake")
async def get_brake(request: Request) -> dict:
    return await _brake(request.app.state.store) or {"on": False}


@router.put("/brake")
async def set_brake(body: dict, request: Request, principal: PrincipalDep) -> dict:
    """Hold every coding agent of the team at once — an incident (a skill gone
    bad, an agent sending things out) — and let them go again. Every sidecar
    sees it with the team's policy, within half a minute; a held call waits
    for the release, then goes on. An owner or admin — and whoever the
    workspace lets pull it (permissions: brake)."""
    store = request.app.state.store
    await _permitted(store, principal, "brake")
    on = bool(body.get("on"))
    value = {"on": on, "by": _by(principal), "ts": datetime.now(UTC).isoformat(),
             "reason": str(body.get("reason") or "")[:200]}
    await store.set_setting(BRAKE, value)
    from axor_backend.funnel import used

    await used(store, "brake_on" if on else "brake_off")
    from axor_backend import audit

    await audit.record(store, request, "brake_on" if on else "brake_off", "every coding agent",
                       **({"reason": value["reason"]} if value["reason"] else {}))
    return value


# ── the signed policy: what widens, as an operator signed it ─────────────────
#
# A sidecar with operator keys in its LOCAL config (axor_wrap.agents.signed)
# takes what narrows from this plane as it is — a skill off, the brake — and
# what widens only from the last policy an operator signed: the team's rules
# (allow, observe, exceptions, trusted sources) and the skills turned on, each
# named by the hash of its files. Changing them here changes nothing for such a
# sidecar until an operator signs the new document; the screen says so.

SIGNED_POLICY = "coding_policy_signed"


def bundle_digest(files: list[dict]) -> str:
    """The hash a signed policy names a team skill's files by — the same as
    axor_wrap.agents.signed.bundle_digest (test-vectored in tests)."""
    from axor_core.kernel.jcs import canonicalize

    rows = sorted(({"path": str(f.get("path", "")), "content": str(f.get("content", ""))}
                   for f in files if isinstance(f, dict)), key=lambda f: f["path"])
    return hashlib.sha256(canonicalize({"files": rows})).hexdigest()


async def _signed(store: Any) -> dict | None:  # noqa: ANN401
    value = await store.get_setting(SIGNED_POLICY)
    return value if isinstance(value, dict) and isinstance(value.get("payload"), dict) else None


async def policy_document(store: Any, seq: int) -> dict:  # noqa: ANN401
    """The document an operator signs: what widens, now."""
    rules = await _rules(store)
    return {
        "kind": "coding_policy", "org": current_org_id(), "seq": seq,
        "rules": ({k: rules[k] for k in ("allow", "mode", "exceptions", "trusted_sources")}
                  if rules["set"] else None),
        "skills_team": [{"name": t["name"], "version": t["live"]["version"],
                         "sha256": bundle_digest(t["live"].get("files") or [])}
                        for t in await store.team_skills() if t["live"]],
    }


def _same(a: dict, b: dict) -> bool:
    return {k: v for k, v in a.items() if k != "seq"} == {k: v for k, v in b.items()
                                                          if k != "seq"}


async def signing_state(store: Any) -> dict:  # noqa: ANN401
    signed = await _signed(store)
    seq = int(signed["payload"].get("seq") or 0) if signed else 0
    current = bool(signed) and _same(signed["payload"], await policy_document(store, seq))
    return {"seq": seq, "current": current,
            **({"operator": signed.get("operator"), "ts": signed.get("ts")} if signed else {})}


@router.get("/policy/document")
async def policy_to_sign(store: StoreDep, principal: PrincipalDep) -> dict:
    """What an operator signs next: the policy as it stands, one seq on."""
    from axor_backend import permissions

    permissions.require_admin(principal)
    state = await signing_state(store)
    return {"payload": await policy_document(store, state["seq"] + 1), "signed": state}


@router.put("/policy/signature")
async def sign_policy(body: dict, request: Request, store: StoreDep,
                      principal: PrincipalDep) -> dict:
    """``{"payload", "operator", "sig"}`` — the document from
    ``/policy/document``, signed. Refused when it is not what stands now, or
    not newer than the one signed before; verified against the plane's
    operator keyring when it has one (the sidecar verifies it in any case)."""
    from axor_backend import audit, permissions
    from axor_backend.signing import CommandRejected

    permissions.require_admin(principal)
    payload, operator, sig = body.get("payload"), body.get("operator"), body.get("sig")
    if not isinstance(payload, dict) or payload.get("kind") != "coding_policy":
        raise HTTPException(400, "payload is the document from /v1/coding/policy/document")
    state = await signing_state(store)
    seq = payload.get("seq")
    if not isinstance(seq, int) or seq <= state["seq"]:
        raise HTTPException(409, "a signed policy is newer than this one — fetch it again")
    if not _same(payload, await policy_document(store, seq)) \
            or payload.get("org") != current_org_id():
        raise HTTPException(409, "the policy changed since this document was fetched — "
                                 "fetch it again and sign that")
    from axor_backend import operators

    if await operators.signing_required(request.app.state, store):
        from axor_core.kernel.jcs import canonicalize

        try:
            await operators.verify(request.app.state, store, operator,
                                   canonicalize(payload), sig)
        except CommandRejected as exc:
            raise HTTPException(403, f"the signature does not verify: {exc}") from exc
    elif not isinstance(operator, str) or not isinstance(sig, str):
        raise HTTPException(400, "operator and sig are strings")
    await store.set_setting(SIGNED_POLICY, {"payload": payload, "operator": operator,
                                            "sig": sig, "ts": datetime.now(UTC).isoformat()})
    await audit.record(store, request, "coding_policy_signed", "", seq=seq, operator=operator)
    return await signing_state(store)


@router.get("/operator-keys")
async def operator_keys(request: Request) -> dict:
    """The operators' public keys and their fingerprint — what the Connect page
    puts into ``axor-wrap connect --operator-key``, so a machine connects in
    the signed posture with no extra step. Trust on first use: a person can
    compare the fingerprint ``axor-wrap connect`` prints with one their admin
    shared out of band."""
    from axor_backend import operators
    from axor_backend.signing import fingerprint

    keys = {**request.app.state.keyring.public_keys(),
            **await operators.active_keys(request.app.state.store)}
    return {"keys": keys, "fingerprint": fingerprint(keys)}
