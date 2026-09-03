"""Notifications — loud elsewhere (spec section 16).

Quiet-until-wrong assumes someone is looking at the screen; the on-call
persona is not. The pairing rule: the UI is quiet, the notification channel is
where wrong gets loud. We emit and route — never a pager.

- Channel: webhook (JSON POST). Slack/Discord/PagerDuty are webhook consumers.
- Triggers (v1): degradation transition upward, Sentinel heat crossing a
  threshold, a run completing with >=1 EvidenceCase, a node stale, a corpus
  run that regressed (regression_failed).
- Routing (EE): subscriptions carry a node glob + channel label; the free
  shape is one global webhook (pattern "*").
- Failure honesty: at-least-once with retries and a dead-letter log visible in
  settings — a notification system that fails silently is worse than none.
  Dead letters persist (capped, migration 0002): a restart must not erase the
  evidence that deliveries were lost.
- Source: the plane event feed the backend already has; a subscriber with an
  HTTP sink and per-trigger debounce, no new instrumentation.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import socket
from collections import deque
from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import Any
from urllib.parse import urlparse

from axor_backend.errors import BackendError
from axor_backend.tenancy import PUBLIC_ORG, current_org_id


class WebhookRefused(BackendError):
    """The webhook URL is not one this backend will dial."""


# A webhook is an operator-supplied URL that the SERVER dials, which makes the
# notification channel a request-forgery primitive. How much of that to block
# depends on whether registering one crosses a privilege boundary:
#
# * Single-tenant self-hosted — the principal with `operate` IS the operator.
#   A webhook to http://alertmanager:9093 on the compose network is the NORMAL
#   case, and refusing it would break the product's primary deployment shape
#   to defend against the operator reaching their own host.
# * Multi-tenant — an organization admin holds `operate` and is emphatically
#   not the infrastructure operator. There the boundary is real, and the whole
#   private range has to be off limits.
#
# So the private-range block is conditional, and the deployment says which it
# is (app passes `block_private`, defaulting to on whenever identity login is
# configured). What is refused UNCONDITIONALLY is the set no deployment has a
# legitimate reason to webhook: non-HTTP schemes, and the link-local range that
# carries the cloud metadata services (169.254.169.254 and its v6 equivalent).
BLOCK_PRIVATE_ENV = "AXOR_WEBHOOK_BLOCK_PRIVATE"
_ALLOWED_SCHEMES = frozenset({"http", "https"})


def _refuse(host: str, address: object, why: str) -> None:
    raise WebhookRefused(
        f"webhook host {host!r} resolves to {address} ({why}) — the "
        f"notification channel must not be usable to reach it."
    )


def check_webhook_url(url: str, block_private: bool = False) -> None:
    """Refuse a webhook target the backend should not dial.

    Always refuses a non-HTTP scheme and any host resolving into link-local,
    reserved or unspecified space — the metadata-service range among them.
    With `block_private`, also refuses loopback and RFC1918/ULA space.

    A host that does not resolve is refused only in blocking mode: there, an
    unresolvable name is a destination this process cannot vouch for, and
    letting it through would make DNS failure the way around the check. In
    permissive mode nothing is being defended, and refusing would break a
    webhook whose collector is simply not up yet.
    """
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise WebhookRefused(f"webhook scheme {parsed.scheme!r} is not http/https")
    host = parsed.hostname
    if not host:
        raise WebhookRefused("webhook URL has no host")
    try:
        infos = socket.getaddrinfo(host, parsed.port or 0, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        if block_private:
            raise WebhookRefused(
                f"webhook host {host!r} does not resolve: {exc}"
            ) from exc
        return
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if address.is_link_local:
            _refuse(host, address, "link-local; this range carries the cloud "
                                   "metadata service")
        if address.is_reserved or address.is_unspecified:
            _refuse(host, address, "a reserved address")
        if block_private and (address.is_loopback or address.is_private):
            raise WebhookRefused(
                f"webhook host {host!r} resolves to the non-public address "
                f"{address}. This deployment blocks internal targets because "
                f"registering a webhook here crosses a privilege boundary. "
                f"Unset {BLOCK_PRIVATE_ENV} only if every principal that can "
                f"subscribe is also the infrastructure operator."
            )


TRIGGERS = frozenset({
    "level_transition_up", "heat_threshold", "evidence_run", "node_stale",
    "regression_failed", "behavioral_drift",
})


@dataclass
class Subscription:
    url: str
    triggers: frozenset[str]
    debounce_seconds: float = 0.0
    # Routing (EE): a named channel + node glob. "*" = everything — the free
    # single-webhook shape. Matching is fnmatch on the emitting node_id.
    label: str = ""
    node_pattern: str = "*"
    # The tenant that registered this webhook. Delivery never crosses it: an
    # organization's on-call must not be paged about another organization's
    # nodes, and the body carries the node id, level and a permalink.
    org: str = PUBLIC_ORG
    # last fire time per (trigger, node) — debounce key. Trace-free: we stamp
    # from a monotonic counter passed in, so tests stay deterministic.
    _last: dict[tuple[str, str], float] = field(default_factory=dict)


@dataclass
class DeadLetter:
    url: str
    payload: dict[str, Any]
    error: str
    attempts: int
    org: str = PUBLIC_ORG


class Notifier:
    """One notifier per backend; subscriptions are per-connection config."""

    def __init__(
        self,
        post: Any = None,  # noqa: ANN401 - injected async POST(url, json) -> status
        max_attempts: int = 4,
        dead_letter_cap: int = 500,
        dead_sink: Any = None,  # noqa: ANN401 - async callable(DeadLetter) that persists it
        block_private: bool = False,
    ) -> None:
        # Whether registering a webhook crosses a privilege boundary in THIS
        # deployment — see check_webhook_url. The app turns it on for any
        # multi-tenant (identity-configured) server.
        self._block_private = block_private
        self._subs: list[Subscription] = []
        self._post = post or _default_post
        self._max_attempts = max_attempts
        self._dead: deque[DeadLetter] = deque(maxlen=dead_letter_cap)
        self._dead_sink = dead_sink
        self._clock = 0.0  # logical clock; debounce is in these units

    def subscribe(
        self, url: str, triggers: list[str], debounce_seconds: float = 0.0,
        label: str = "", node_pattern: str = "*", org: str | None = None,
    ) -> None:
        bad = set(triggers) - TRIGGERS
        if bad:
            raise ValueError(f"unknown triggers: {sorted(bad)}")
        check_webhook_url(url, self._block_private)
        self._subs.append(Subscription(
            url, frozenset(triggers), debounce_seconds,
            label=label, node_pattern=node_pattern or "*",
            org=org if org is not None else current_org_id(),
        ))

    def tick(self, dt: float = 1.0) -> None:
        self._clock += dt

    @property
    def dead_letters(self) -> list[DeadLetter]:
        return list(self._dead)

    async def emit(
        self, trigger: str, node_id: str, payload: dict[str, Any],
        org: str | None = None,
    ) -> int:
        """Fan a triggering event out to this tenant's matching subscriptions.
        Returns the number of subscriptions delivered to (after debounce).

        `org` defaults to the ambient tenant, which is right inside a request
        and inside the background sweeps (they set it per iteration). Pass it
        explicitly when emitting from somewhere neither holds."""
        if trigger not in TRIGGERS:
            raise ValueError(f"unknown trigger {trigger!r}")
        scope = org if org is not None else current_org_id()
        delivered = 0
        body = {"trigger": trigger, "node_id": node_id, **payload}
        for sub in self._subs:
            if sub.org != scope:
                continue
            if trigger not in sub.triggers:
                continue
            if not fnmatch(node_id, sub.node_pattern):
                continue
            key = (trigger, node_id)
            if sub.debounce_seconds > 0:
                last = sub._last.get(key)
                if last is not None and (self._clock - last) < sub.debounce_seconds:
                    continue
                sub._last[key] = self._clock
            await self._deliver(sub.url, body, sub.org)
            delivered += 1
        return delivered

    async def _deliver(
        self, url: str, body: dict[str, Any], org: str = PUBLIC_ORG
    ) -> None:
        backoff = 0.05
        last_error = ""
        for attempt in range(1, self._max_attempts + 1):
            try:
                # Re-checked per delivery, not only at subscribe: a hostname
                # that resolved publicly then can resolve to a private address
                # now. This narrows the window rather than closing it — the
                # resolution the check makes is not the one httpx will make
                # (a DNS-rebinding TOCTOU that only a pinned-IP transport
                # closes), so it is defence in depth, not a guarantee.
                check_webhook_url(url, self._block_private)
                status = await self._post(url, body)
                if 200 <= status < 300:
                    return
                last_error = f"status {status}"
            except Exception as exc:  # noqa: BLE001 - honest dead-letter
                last_error = f"{type(exc).__name__}: {exc}"
            if attempt < self._max_attempts:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 2.0)
        letter = DeadLetter(url=url, payload=body, error=last_error,
                            attempts=self._max_attempts, org=org)
        self._dead.append(letter)
        if self._dead_sink is not None:
            # Persistence must never make emit() itself fail — a broken DB on
            # top of a broken webhook still leaves the in-memory record.
            try:
                await self._dead_sink(letter)
            except Exception:  # noqa: BLE001 - deliberately non-fatal
                logging.getLogger("axor.notifications").exception(
                    "dead-letter persist failed"
                )


async def _default_post(url: str, body: dict[str, Any]) -> int:
    import httpx

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            url, content=json.dumps(body).encode(),
            headers={"content-type": "application/json"},
        )
        return resp.status_code
