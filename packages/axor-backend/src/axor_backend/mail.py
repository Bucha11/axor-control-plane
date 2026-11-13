"""Alert mail: the e-mail half of the notification channel.

A subscription whose url is ``mailto:<address>`` is delivered by mail instead of
a POST. ``AXOR_SMTP_URL`` (``AppConfig.smtp_url``) picks the transport — ``smtp://user:pass@host:587``
(STARTTLS), ``smtps://…:465``, or ``log`` (written to the log, for a local
install). Unset, mail subscriptions are refused at subscribe, so an alert can
never be accepted and then silently not sent. ``AXOR_MAIL_FROM`` is the sender.

Stdlib ``smtplib`` in a worker thread: alerts are a few messages an hour, and a
mail library is not worth a dependency for that.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any, Protocol
from urllib.parse import unquote, urlsplit

log = logging.getLogger("axor.mail")

# one plain address; no display names, no lists — a subscription is one inbox
_ADDRESS = re.compile(r"^[^@\s,;<>\"]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}$")


class Mailer(Protocol):
    async def send(self, to: str, subject: str, body: str) -> None: ...


class LogMailer:
    async def send(self, to: str, subject: str, body: str) -> None:
        log.warning("alert mail (not sent — AXOR_SMTP_URL=log) to=%s subject=%s\n%s",
                    to, subject, body)


@dataclass
class SmtpMailer:
    host: str
    port: int
    tls: str  # starttls | ssl | none
    username: str
    password: str
    sender: str
    timeout: float = 20.0

    async def send(self, to: str, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.sender, to, subject
        msg.set_content(body)
        await asyncio.to_thread(self._send, msg)

    def _send(self, msg: EmailMessage) -> None:
        ctx = ssl.create_default_context()
        if self.tls == "ssl":
            conn: smtplib.SMTP = smtplib.SMTP_SSL(
                self.host, self.port, timeout=self.timeout, context=ctx)
        else:
            conn = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
        with conn:
            if self.tls == "starttls":
                conn.starttls(context=ctx)
            if self.username:
                conn.login(self.username, self.password)
            conn.send_message(msg)


def mailer_from_url(url: str | None, sender: str | None = None) -> Mailer | None:
    url = (url or "").strip()
    if not url:
        return None
    if url == "log":
        return LogMailer()
    parts = urlsplit(url)
    if parts.scheme not in ("smtp", "smtps") or not parts.hostname:
        raise ValueError("AXOR_SMTP_URL must be smtp://…, smtps://… or 'log'")
    tls = "ssl" if parts.scheme == "smtps" else ("none" if parts.port == 25 else "starttls")
    return SmtpMailer(
        host=parts.hostname, port=parts.port or (465 if parts.scheme == "smtps" else 587),
        tls=tls, username=unquote(parts.username or ""),
        password=unquote(parts.password or ""),
        sender=sender or f"alerts@{parts.hostname}")


def mail_address(url: str) -> str | None:
    """The address of a ``mailto:`` subscription url, or None if it is not one.
    Raises ValueError for a mailto: that is not one plain address."""
    if not url.lower().startswith("mailto:"):
        return None
    address = unquote(url[len("mailto:"):]).strip()
    if not _ADDRESS.match(address):
        raise ValueError("a mail subscription is mailto:<one address>")
    return address.lower()


_TITLES = {
    "behavioral_drift": "behaviour drifted",
    "level_transition_up": "degradation level went up",
    "heat_threshold": "Sentinel heat crossed its threshold",
    "evidence_run": "a run produced evidence",
    "node_stale": "agent went quiet",
    "regression_failed": "a regression check failed",
    "license_expiring": "license expiring",
}


def render(body: dict[str, Any]) -> tuple[str, str]:
    """(subject, text) for one notification body."""
    trigger = str(body.get("trigger", "alert"))
    node = str(body.get("node_id", ""))
    subject = f"[Axor] {node}: {_TITLES.get(trigger, trigger)}"
    lines = [f"{_TITLES.get(trigger, trigger)} — {node}", ""]
    link = body.get("permalink") or body.get("url")
    if isinstance(link, str) and link:
        lines += [f"Open: {link}", ""]
    details = {k: v for k, v in body.items() if k not in ("permalink",)}
    lines += ["Details:", json.dumps(details, indent=2, sort_keys=True, default=str), "",
              "You get this because a notification subscription in the Axor "
              "control plane sends this trigger to this address. Remove it in "
              "Settings → Notifications."]
    return subject, "\n".join(lines)
