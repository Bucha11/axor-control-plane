"""Guard mode — the proxy as a gate in front of the agent's tools, not only a
witness.

By default the proxy observes: it forwards, records, injects test faults, and
never refuses a call. Started with ``--guard`` and the agent's tool manifests,
every call that reaches ``/t/{tool}`` is decided first, always — no armed run:

- the agent was paused or stopped from the Control Plane → 503, nothing sent
- the call budget set from the plane is spent → 429
- the governor denies it → 403 (an MCP ``tools/call`` gets a JSON-RPC error
  instead, which is what an MCP client reads) and the upstream is never called

The governor is the same ``ToolCallGovernor`` a ``WrappedToolset`` runs, built
from the same manifests (the Config Builder's, or ``axor-wrap manifest``). What
a proxy cannot give it is a callable; what it can give it is the arguments of
the call (an MCP ``tools/call``, a JSON object body, or the query string) and
the text of every answer. Answers are registered as TEXT — a JSON answer as its
string leaves joined — because the per-value ledger matches fragments within a
string and treats any other object as one opaque value: an address lifted out
of a search result's JSON would otherwise pass.

What the guard cannot see is the model: it holds the agent's tool calls, not
its context, so pausing the agent stops its tools and not its talking, and a
bad item in the agent's context cannot be cut from here — that is the wrap's
side (axor-wrap / an adapter). A plane outage does not open the gate: the
governor is local; the plane only adds pause / stop / budget, and a link that
cannot be built is logged and the guard keeps enforcing.

Sessions: ``X-Axor-Session`` names the conversation a call belongs to, so one
user's untrusted text does not taint another's calls. Without it calls share
one session, which ends after ``idle_s`` of quiet.

The trace shipped to the plane keeps the proxy's privacy rule: argument names
with sizes, never their values; no answer previews.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_log = logging.getLogger("axor.proxy.guard")

DEFAULT_SESSION = "default"


def load_manifests(path: Path) -> list[dict[str, Any]]:
    """A manifests bundle (``{"manifests": [...]}``, as the Config Builder
    saves it), a bare list, or a directory of ``*.manifest.json`` files."""
    if path.is_dir():
        return [json.loads(p.read_text(encoding="utf-8"))
                for p in sorted(path.glob("*.manifest.json"))]
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("manifests"), list):
        return list(data["manifests"])
    if isinstance(data, list):
        return data
    raise ValueError(f"{path}: expected a manifests bundle, a list, or a directory")


def answer_text(value: Any) -> str:  # noqa: ANN401 - arbitrary tool JSON
    """The text an answer carries, for the ledger: a string as is, a JSON
    value as its string leaves joined (keys are structure, not content)."""
    if isinstance(value, str):
        return value
    leaves: list[str] = []

    def walk(v: Any) -> None:  # noqa: ANN401
        if isinstance(v, str):
            leaves.append(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif v is not None and not isinstance(v, bool):
            leaves.append(str(v))

    walk(value)
    return "\n".join(leaves)


def _redacted(args: dict[str, Any]) -> dict[str, str]:
    out = {}
    for k, v in args.items():
        raw = v if isinstance(v, str) else json.dumps(v, default=str)
        out[str(k)] = f"<{len(raw)} chars sha256:{hashlib.sha256(raw.encode()).hexdigest()[:8]}>"
    return out


@dataclass
class _Session:
    key: str
    governor: Any  # noqa: ANN401 - ToolCallGovernor
    run_id: str
    last_used: float = field(default_factory=time.time)
    calls: int = 0
    seq: int = 0


@dataclass
class Refusal:
    status: int
    error: str
    detail: str
    category: str = ""


class Guard:
    def __init__(
        self,
        manifests: list[dict[str, Any]],
        node_id: str,
        *,
        plane_url: str | None = None,
        ingest_key: str | None = None,
        idle_s: float = 900.0,
        link: Any = None,  # noqa: ANN401 - PlaneLink, injectable for tests
    ) -> None:
        if not manifests:
            raise ValueError("guard mode needs the tools' manifests: without them "
                             "nothing says which tool reads outside text or sends "
                             "something out, and nothing could be refused")
        self.manifests = manifests
        self.node_id = node_id
        self.idle_s = idle_s
        self.tools = sorted(str(m.get("id")) for m in manifests)
        self._lock = threading.Lock()
        self._sessions: dict[str, _Session] = {}
        self.link = link
        if self.link is None and plane_url:
            try:
                from axor_wrap.plane.link import PlaneLink

                self.link = PlaneLink(plane_url, node_id, ingest_key=ingest_key)
            except Exception as exc:  # noqa: BLE001 - enforcement is local
                _log.warning("axor guard: no Control-Plane link (%s) — still "
                             "enforcing the manifests, without pause / stop", exc)

    # ── sessions ──────────────────────────────────────────────────────────────

    def session(self, key: str | None) -> _Session:
        from axor_proxy.runs import build_governor

        key = (key or DEFAULT_SESSION)[:120]
        now = time.time()
        with self._lock:
            for k in [k for k, s in self._sessions.items() if now - s.last_used > self.idle_s]:
                del self._sessions[k]
            s = self._sessions.get(key)
            if s is None:
                s = _Session(key=key, governor=build_governor(self.manifests, self.node_id),
                             run_id=f"{self.node_id}-{uuid.uuid4().hex[:10]}")
                self._sessions[key] = s
            s.last_used = now
            return s

    # ── the gate ──────────────────────────────────────────────────────────────

    def posture_refusal(self, s: _Session) -> Refusal | None:
        if self.link is None:
            return None
        posture = self.link.posture()
        if posture == "stopped":
            return Refusal(503, "agent_stopped",
                           "this agent was stopped from the Control Plane", "capability")
        if posture == "paused":
            return Refusal(503, "agent_paused",
                           "this agent is paused from the Control Plane — retry once resumed",
                           "capability")
        cap = self.link.budget_cap_calls()
        if cap is not None and s.calls >= cap:
            return Refusal(429, "budget_spent",
                           f"the call budget set from the Control Plane ({cap}) is spent", "budget")
        return None

    def check(self, s: _Session, tool: str, args: dict[str, Any]) -> tuple[Any, Refusal | None]:
        """(decision, refusal). A refusal means: do not call the upstream."""
        refusal = self.posture_refusal(s)
        if refusal is not None:
            self._ship(s, tool, args, None, gate=refusal.category, denied=True)
            return None, refusal
        decision = s.governor.evaluate(tool, args)
        if not decision.allowed:
            from axor_core.governor import gate_of

            self._ship(s, tool, args, None, gate=gate_of(decision.category), denied=True)
            return decision, Refusal(403, "denied_by_policy", decision.reason, decision.category)
        s.calls += 1
        return decision, None

    def done(self, s: _Session, tool: str, args: dict[str, Any],
             decision: Any,  # noqa: ANN401 - GovernanceDecision
             answer: Any,  # noqa: ANN401 - arbitrary tool JSON
             status: int | None) -> None:
        """Fold an executed call's answer into the session's ledger."""
        ok = status is not None and status < 400
        if ok and decision is not None:
            s.governor.register_output(decision, answer_text(answer))
        self._ship(s, tool, args, "ok" if ok else "error", gate="", denied=False)

    # ── the plane trace (no values: the proxy's privacy rule) ─────────────────

    def _ship(self, s: _Session, tool: str, args: dict[str, Any], status: str | None,
              *, gate: str, denied: bool) -> None:
        link = self.link
        if link is None:
            return
        s.seq += 1
        call_id = f"g{s.seq}"
        call: dict[str, Any] = {"kind": "tool_call",
                                "payload": {"tool": tool, "args": _redacted(args),
                                            "tool_call_id": call_id}}
        lines = [call]
        if denied:
            call.update(verdict="deny", gate=gate or "capability")
        else:
            lines.append({"kind": "tool_result", "causal_root": call_id,
                          "payload": {"tool": tool, "status": status, "value_ref": call_id}})
        run_id = s.run_id
        try:
            link.submit(lambda: link.connector.post_trace(lines, run_id))
        except Exception:  # noqa: BLE001 - a plane hiccup must not fail a call
            _log.debug("axor guard: trace not shipped", exc_info=True)

    def describe(self) -> dict[str, Any]:
        return {"node_id": self.node_id, "tools": self.tools,
                "plane": self.link is not None,
                "posture": self.link.posture() if self.link is not None else "running",
                "sessions": len(self._sessions)}
