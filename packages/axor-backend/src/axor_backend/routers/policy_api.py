"""The workspace's tool policy — what each of an agent's tools does.

It lived only in the browser that wrote it (the frontend's persisted store).
A second person — the security lead the receipt was sent to, a teammate, the
same person on another machine — opened Policy and got it rebuilt from the
agent's runs instead: a refund tool as "reads", the ticket's outside-text field
and the email's deciding argument gone. The guard enforced the manifests it
was started with while Policy, and "Safe to ship?" checking "the rules from
Policy", showed different rules. Kept here, it is one policy for everyone.

The shape is the frontend's ``ToolClass`` per tool name; validated so a typo
is a 400 naming the field, not a policy that quietly reads as something else.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from axor_backend import audit
from axor_backend.clock import now
from axor_backend.deps import StoreDep

router = APIRouter(prefix="/v1/policy", tags=["policy"])

SETTING = "tool_policy"
KLASSES = ("READ", "WRITE", "EXPORT", "EXEC")
_STRINGS = ("url", "source", "consequence")
_LISTS = ("outsideFields", "driving")
MAX_TOOLS = 500


def _tool(name: str, raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise HTTPException(400, f"tools[{name!r}] must be an object")
    if raw.get("klass") not in KLASSES:
        raise HTTPException(400, f"tools[{name!r}].klass must be one of {'|'.join(KLASSES)}")
    out: dict[str, Any] = {"klass": raw["klass"], "outside": bool(raw.get("outside", False))}
    for key in _STRINGS:
        if raw.get(key) is not None:
            if not isinstance(raw[key], str):
                raise HTTPException(400, f"tools[{name!r}].{key} must be a string")
            out[key] = raw[key][:2000]
    for key in _LISTS:
        if raw.get(key) is not None:
            v = raw[key]
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                raise HTTPException(400, f"tools[{name!r}].{key} must be a list of strings")
            out[key] = v[:100]
    if raw.get("trusted") is not None:
        t = raw["trusted"]
        if not isinstance(t, dict) or not all(
                isinstance(k, str) and isinstance(v, list) and all(isinstance(x, str) for x in v)
                for k, v in t.items()):
            raise HTTPException(
                400, f"tools[{name!r}].trusted must map arguments to lists of values")
        out["trusted"] = t
    if isinstance(raw.get("args_schema"), dict):
        out["args_schema"] = raw["args_schema"]
    return out


@router.get("")
async def get_policy(store: StoreDep) -> dict:
    saved = await store.get_setting(SETTING)
    if not isinstance(saved, dict):
        return {"tools": {}, "updated_ts": None}
    return {"tools": saved.get("tools") or {}, "updated_ts": saved.get("updated_ts")}


@router.put("")
async def put_policy(body: dict, request: Request, store: StoreDep) -> dict:
    tools = body.get("tools")
    if not isinstance(tools, dict) or len(tools) > MAX_TOOLS:
        raise HTTPException(400, f"tools must be an object of at most {MAX_TOOLS} tools")
    clean = {str(name)[:200]: _tool(str(name), raw) for name, raw in tools.items()}
    before = await store.get_setting(SETTING)
    prior = (before or {}).get("tools") or {} if isinstance(before, dict) else {}
    changed = sorted(n for n in set(prior) | set(clean) if prior.get(n) != clean.get(n))
    if not changed:
        return {"tools": clean, "updated_ts": (before or {}).get("updated_ts"), "changed": []}
    ts = now()
    await store.set_setting(SETTING, {"tools": clean, "updated_ts": ts})
    await audit.record(store, request, "policy_saved", ", ".join(changed)[:300])
    return {"tools": clean, "updated_ts": ts, "changed": changed}
