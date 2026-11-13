"""E-mail links: verification on signup, resend, and password reset."""
from __future__ import annotations

import pathlib
import re
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from axor_identity.app import create_app
from axor_identity.keys import generate_pem, load_signing_key
from axor_identity.mail import LogMailer, SmtpMailer, mailer_from_url

PUBLIC = "https://cp.example.com/identity"
PW = "correct horse battery"


class Outbox:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    async def send(self, to: str, subject: str, body: str) -> None:
        self.sent.append((to, subject, body))

    def link(self, param: str) -> str:
        url = re.search(r"https://\S+", self.sent[-1][2]).group(0)
        return parse_qs(urlsplit(url).query)[param][0]


@pytest.fixture
def outbox() -> Outbox:
    return Outbox()


@pytest.fixture
async def client(tmp_path: pathlib.Path, outbox: Outbox):  # noqa: ANN201
    app = create_app(f"sqlite+aiosqlite:///{tmp_path}/id.db",
                     signing_key=load_signing_key(generate_pem(), kid="k"),
                     public_url=PUBLIC, providers={}, mailer=outbox)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://identity") as c:
            yield c


async def _signup(client: httpx.AsyncClient, email: str = "ada@acme.io") -> dict:
    r = await client.post("/v1/signup", json={
        "email": email, "password": PW, "org_name": "Acme", "return_to": "/#/settings"})
    assert r.status_code == 201, r.text
    return r.json()


def _auth(session: dict) -> dict:
    return {"Authorization": f"Bearer {session['access_token']}"}


async def test_signup_mails_a_link_that_verifies_the_address(client, outbox) -> None:  # noqa: ANN001
    s = await _signup(client)
    to, subject, body = outbox.sent[-1]
    assert to == "ada@acme.io" and "Confirm" in subject
    url = urlsplit(re.search(r"https://\S+", body).group(0))
    assert url.netloc == "cp.example.com" and url.fragment == "/settings"
    me = (await client.get("/v1/me", headers=_auth(s))).json()
    assert me["user"]["email_verified"] is False
    r = await client.post("/v1/email/verify", json={"token": outbox.link("axor_verify")})
    assert r.status_code == 200 and r.json()["email_verified"] is True
    me = (await client.get("/v1/me", headers=_auth(s))).json()
    assert me["user"]["email_verified"] is True
    # single use
    again = await client.post("/v1/email/verify", json={"token": outbox.link("axor_verify")})
    assert again.status_code == 400


async def test_resend_is_paced_and_stops_once_verified(client, outbox) -> None:  # noqa: ANN001
    s = await _signup(client)
    # signup just sent one: a resend within the minute is refused
    r = await client.post("/v1/email/verify/send", json={}, headers=_auth(s))
    assert r.status_code == 429
    await client.post("/v1/email/verify", json={"token": outbox.link("axor_verify")})
    r = await client.post("/v1/email/verify/send", json={}, headers=_auth(s))
    assert r.status_code == 409


async def test_password_reset_sets_a_new_password_and_ends_old_sessions(
        client, outbox) -> None:  # noqa: ANN001
    s = await _signup(client)
    r = await client.post("/v1/password/forgot", json={"email": "ADA@acme.io"})
    assert r.status_code == 202
    assert "reset" in outbox.sent[-1][1].lower()
    token = outbox.link("axor_reset")
    r = await client.post("/v1/password/reset", json={"token": token, "password": "new password 1"})
    assert r.status_code == 204
    assert (await client.post("/v1/login", json={
        "email": "ada@acme.io", "password": PW})).status_code == 401
    assert (await client.post("/v1/login", json={
        "email": "ada@acme.io", "password": "new password 1"})).status_code == 200
    assert (await client.post("/v1/refresh", json={
        "refresh_token": s["refresh_token"]})).status_code == 401
    # following the link proved the address
    me = (await client.post("/v1/login", json={
        "email": "ada@acme.io", "password": "new password 1"})).json()
    assert (await client.get("/v1/me", headers=_auth(me))).json()["user"]["email_verified"]
    # single use
    r = await client.post("/v1/password/reset", json={"token": token, "password": "another one"})
    assert r.status_code == 400


async def test_forgot_answers_the_same_for_a_stranger(client, outbox) -> None:  # noqa: ANN001
    await _signup(client)
    sent = len(outbox.sent)
    known = await client.post("/v1/password/forgot", json={"email": "ada@acme.io"})
    unknown = await client.post("/v1/password/forgot", json={"email": "nobody@acme.io"})
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()
    assert len(outbox.sent) == sent + 1  # only the real account got mail


async def test_links_only_open_our_own_pages(client) -> None:  # noqa: ANN001
    r = await client.post("/v1/password/forgot", json={
        "email": "ada@acme.io", "return_to": "https://evil.example.org/"})
    assert r.status_code == 400


async def test_no_mail_configured_means_no_mail_features(tmp_path: pathlib.Path) -> None:
    app = create_app(f"sqlite+aiosqlite:///{tmp_path}/n.db",
                     signing_key=load_signing_key(generate_pem(), kid="k"),
                     public_url=PUBLIC, providers={}, mailer=None)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://identity") as c:
            assert (await c.get("/v1/email")).json() == {"available": False}
            r = await c.post("/v1/password/forgot", json={"email": "a@b.io"})
            assert r.status_code == 503
            # signup still works without mail
            s = await c.post("/v1/signup", json={
                "email": "a@b.io", "password": PW, "org_name": "A"})
            assert s.status_code == 201


def test_the_smtp_url_is_read_or_refused() -> None:
    assert mailer_from_url("") is None
    assert isinstance(mailer_from_url("log"), LogMailer)
    m = mailer_from_url("smtp://user%40x:p%40ss@mail.example.com:587")
    assert isinstance(m, SmtpMailer) and m.tls == "starttls" and m.port == 587
    assert m.username == "user@x" and m.password == "p@ss"
    assert m.sender == "no-reply@mail.example.com"
    assert mailer_from_url("smtps://mail.example.com").tls == "ssl"
    with pytest.raises(ValueError):
        mailer_from_url("mail.example.com")
