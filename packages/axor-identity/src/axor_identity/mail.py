"""Sending the identity service's e-mail: address verification and password reset.

``AXOR_IDENTITY_SMTP_URL`` picks the transport:

- ``smtp://user:pass@host:587``   STARTTLS (the usual submission port)
- ``smtps://user:pass@host:465``  TLS from the first byte
- ``log``                         no mail: the message is written to the log, for
                                  a local install whose operator reads the link there
- unset                           no mail at all — verification and reset are off,
                                  and ``/v1/email`` says so, so a client offers neither

``AXOR_IDENTITY_MAIL_FROM`` is the sender (default ``no-reply@<smtp host>``).
The SMTP exchange runs in a worker thread with stdlib ``smtplib``: no new
dependency for a few messages an hour.
"""
from __future__ import annotations

import asyncio
import logging
import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol
from urllib.parse import unquote, urlsplit

log = logging.getLogger("axor_identity.mail")


class Mailer(Protocol):
    async def send(self, to: str, subject: str, body: str) -> None: ...


class LogMailer:
    async def send(self, to: str, subject: str, body: str) -> None:
        log.warning("mail (not sent — AXOR_IDENTITY_SMTP_URL=log) to=%s subject=%s\n%s",
                    to, subject, body)


@dataclass
class SmtpMailer:
    host: str
    port: int
    tls: str            # "starttls" | "ssl" | "none"
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
            conn: smtplib.SMTP = smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout,
                                                  context=ctx)
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
        # a typo here is mail that silently never goes out: fail at boot instead
        raise ValueError("AXOR_IDENTITY_SMTP_URL must be smtp://…, smtps://… or 'log'")
    tls = "ssl" if parts.scheme == "smtps" else (
        "none" if parts.port == 25 else "starttls")
    port = parts.port or (465 if parts.scheme == "smtps" else 587)
    return SmtpMailer(
        host=parts.hostname, port=port, tls=tls,
        username=unquote(parts.username or ""), password=unquote(parts.password or ""),
        sender=sender or f"no-reply@{parts.hostname}")


def mailer_from_env() -> Mailer | None:
    return mailer_from_url(os.environ.get("AXOR_IDENTITY_SMTP_URL"),
                           os.environ.get("AXOR_IDENTITY_MAIL_FROM") or None)
