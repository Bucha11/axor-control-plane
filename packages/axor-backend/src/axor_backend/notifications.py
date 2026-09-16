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
- Delivery is OFF the caller's path. `emit` matches and schedules; the POSTs,
  their retries and their dead-lettering happen in background tasks. Awaiting
  them inline meant a customer's broken webhook stalled their own governed
  nodes: `emit` is called from the telemetry handler, and four attempts at a
  ten-second timeout is forty seconds on a heartbeat whose stale window is
  thirty.
- Source: the plane event feed the backend already has; a subscriber with an
  HTTP sink and per-trigger debounce, no new instrumentation.
"""
from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import logging
import socket
import time
from collections import deque
from collections.abc import Callable
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
    # An expiring license is the only trigger that is not about a governed
    # system. It is here because expiry was silent: EE went read-only and the
    # operator found out from a 402. `node_id` carries the licensed
    # organization, which is the subject of this one.
    "license_expiring",
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
    # last fire time per (trigger, node) — the debounce key, stamped from the
    # notifier's clock. Pruned against the window so a long-lived process with a
    # large fleet does not accumulate an entry per (trigger, node) forever.
    _last: dict[tuple[str, str], float] = field(default_factory=dict)

    def key(self) -> tuple[str, str, frozenset[str], str]:
        """What makes two subscriptions the same one — the same identity the
        store's unique constraint uses, so memory and the database agree about
        how many deliveries a repeated subscribe produces."""
        return (self.org, self.url, self.triggers, self.node_pattern)

    def due(self, trigger: str, node_id: str, now: float) -> bool:
        """Whether this event fires, and stamp it if so."""
        if self.debounce_seconds <= 0:
            return True
        slot = (trigger, node_id)
        last = self._last.get(slot)
        if last is not None and (now - last) < self.debounce_seconds:
            return False
        self._last[slot] = now
        if len(self._last) > _DEBOUNCE_KEYS_MAX:
            cutoff = now - self.debounce_seconds
            self._last = {
                k: v for k, v in self._last.items() if v >= cutoff
            } or {slot: now}
        return True


# Debounce keys held per subscription before the expired ones are swept. A
# fleet emits (trigger, node) pairs without bound over a long process life.
_DEBOUNCE_KEYS_MAX = 4096

# In-flight deliveries across all subscriptions. Past this a delivery is
# dead-lettered immediately rather than queued: a notifier that answers a flood
# by growing a task list without limit trades a visible failure for an
# invisible one.
_MAX_IN_FLIGHT = 256


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
        clock: Callable[[], float] | None = None,
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
        # Debounce needs a clock that MOVES. This was a logical counter advanced
        # by a `tick()` that only a test ever called, so in every deployment it
        # stood at zero: the first event for a (trigger, node) stamped 0.0 and
        # every later one measured a zero-length interval against the window and
        # was suppressed. A debounce of any size was a permanent mute, and it
        # looked exactly like configuration working.
        self._now: Callable[[], float] = clock or time.monotonic
        self._in_flight: set[asyncio.Task] = set()

    def validate(self, url: str, triggers: list[str]) -> None:
        """Everything :meth:`subscribe` would reject, without registering.

        A caller that persists a subscription before registering it needs to
        know the answer first, so it does not write a row for a webhook this
        notifier will refuse to hold.
        """
        bad = set(triggers) - TRIGGERS
        if bad:
            raise ValueError(f"unknown triggers: {sorted(bad)}")
        check_webhook_url(url, self._block_private)

    def subscribe(
        self, url: str, triggers: list[str], debounce_seconds: float = 0.0,
        label: str = "", node_pattern: str = "*", org: str | None = None,
    ) -> None:
        """Register, or update the one already here.

        Idempotent on the same identity the store's unique constraint uses. It
        used to append unconditionally, so subscribing the same webhook twice
        stored ONE row and delivered TWICE — and a restart, which rehydrates
        from those rows, silently went back to once. The duplicate rate
        depended on how recently the process had been restarted.
        """
        self.validate(url, triggers)
        fresh = Subscription(
            url, frozenset(triggers), debounce_seconds,
            label=label, node_pattern=node_pattern or "*",
            org=org if org is not None else current_org_id(),
        )
        for existing in self._subs:
            if existing.key() == fresh.key():
                # Keep `_last`: re-subscribing is not a way to clear a debounce
                # that is doing its job.
                existing.debounce_seconds = debounce_seconds
                existing.label = label
                return
        self._subs.append(fresh)

    def unsubscribe(self, url: str, org: str | None = None) -> int:
        """Stop delivering to `url` for this tenant. Returns how many went.

        There was no way to do this at all: a webhook registered once fired
        forever, and a wrong or leaked URL could only be removed by editing the
        database — while the body it receives carries node ids, levels, a
        permalink, and for `license_expiring` the licensed organization.
        """
        scope = org if org is not None else current_org_id()
        before = len(self._subs)
        self._subs = [
            s for s in self._subs if not (s.url == url and s.org == scope)
        ]
        return before - len(self._subs)

    @property
    def dead_letters(self) -> list[DeadLetter]:
        return list(self._dead)

    async def drain(self, seconds: float = 10.0) -> None:
        """Wait for the deliveries already scheduled. Shutdown calls this so a
        notification in flight is not simply dropped when the process stops;
        tests call it to observe a delivery that emit() no longer waits for."""
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(seconds):
                while self._in_flight:
                    await asyncio.wait(list(self._in_flight))

    async def emit(
        self, trigger: str, node_id: str, payload: dict[str, Any],
        org: str | None = None,
    ) -> int:
        """Fan a triggering event out to this tenant's matching subscriptions.
        Returns the number of subscriptions it was SCHEDULED to (after
        debounce) — the POSTs run in the background.

        Scheduled, not awaited, and that is the point. This used to deliver
        inline, and `emit` is called from the telemetry handler: four attempts
        at a ten-second timeout is forty seconds added to a heartbeat whose
        stale window is thirty, so one customer's dead webhook drove their own
        governed nodes into `node_stale` — which emitted again, into the same
        dead webhook. A broken notification sink looked like a failing fleet.

        `org` defaults to the ambient tenant, which is right inside a request
        and inside the background sweeps (they set it per iteration). Pass it
        explicitly when emitting from somewhere neither holds. It is resolved
        HERE rather than in the task, because by the time the task runs the
        request's ContextVar is gone.
        """
        if trigger not in TRIGGERS:
            raise ValueError(f"unknown trigger {trigger!r}")
        scope = org if org is not None else current_org_id()
        now = self._now()
        scheduled = 0
        body = {"trigger": trigger, "node_id": node_id, **payload}
        for sub in self._subs:
            if sub.org != scope:
                continue
            if trigger not in sub.triggers:
                continue
            if not fnmatch(node_id, sub.node_pattern):
                continue
            if not sub.due(trigger, node_id, now):
                continue
            self._schedule(sub.url, body, sub.org)
            scheduled += 1
        return scheduled

    def _schedule(self, url: str, body: dict[str, Any], org: str) -> None:
        """Hand one delivery to the event loop, or dead-letter it now.

        Saturation is dead-lettered rather than queued: a notifier that answers
        a flood by growing a task list without limit trades a visible failure
        for an invisible one, and the whole point of the dead-letter log is
        that lost deliveries are evidence.
        """
        if len(self._in_flight) >= _MAX_IN_FLIGHT:
            self._bury(DeadLetter(
                url=url, payload=body, org=org, attempts=0,
                error=f"notifier saturated ({_MAX_IN_FLIGHT} deliveries in "
                      f"flight); this one was not attempted",
            ))
            return
        task = asyncio.create_task(self._deliver(url, body, org))
        # Held so the loop does not garbage-collect a delivery mid-flight, and
        # so `drain` can wait for what is outstanding.
        self._in_flight.add(task)
        task.add_done_callback(self._in_flight.discard)

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
        await self._bury_and_persist(DeadLetter(
            url=url, payload=body, error=last_error,
            attempts=self._max_attempts, org=org,
        ))

    def _bury(self, letter: DeadLetter) -> None:
        """Record a lost delivery in memory. Synchronous, so the saturation
        path can use it without needing a task of its own."""
        self._dead.append(letter)
        if self._dead_sink is not None:
            self._schedule_persist(letter)

    def _schedule_persist(self, letter: DeadLetter) -> None:
        task = asyncio.create_task(self._persist(letter))
        self._in_flight.add(task)
        task.add_done_callback(self._in_flight.discard)

    async def _bury_and_persist(self, letter: DeadLetter) -> None:
        self._dead.append(letter)
        if self._dead_sink is not None:
            await self._persist(letter)

    async def _persist(self, letter: DeadLetter) -> None:
        # Persistence must never take down the delivery task — a broken DB on
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
