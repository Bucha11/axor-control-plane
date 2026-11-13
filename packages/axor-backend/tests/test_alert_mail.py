"""Alert mail: a mailto: subscription is delivered by mail, only where the
deployment sends mail, and a signed-in person may only mail their own,
confirmed address."""
from __future__ import annotations

import pathlib

import httpx
import pytest
from axor_backend.app import create_app
from axor_backend.mail import mail_address, mailer_from_url, render
from test_identity_auth import TOKEN, _bearer, _jwks, _mint

pytest.importorskip("jwt")
from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey,
)


class Outbox:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    async def send(self, to: str, subject: str, body: str) -> None:
        self.sent.append((to, subject, body))


@pytest.fixture
def priv() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.fixture
def outbox() -> Outbox:
    return Outbox()


@pytest.fixture
async def setup(tmp_path: pathlib.Path, priv: Ed25519PrivateKey, outbox: Outbox):  # noqa: ANN201
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/m.db",
                     operator_keys={}, allow_unsigned=True, api_token=TOKEN,
                     identity_jwks=_jwks(priv))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cp.test") as c, \
            app.router.lifespan_context(app):
        app.state.notifier._mailer = outbox
        yield app, c


SUB = {"triggers": ["behavioral_drift"]}


async def test_your_own_confirmed_address_gets_the_alert(setup, priv, outbox) -> None:  # noqa: ANN001
    app, c = setup
    r = await c.post("/v1/notifications/subscribe",
                     headers=_bearer(_mint(priv, evf=True)),
                     json={**SUB, "url": "mailto:A@acme.io"})
    assert r.status_code == 200, r.text
    n = await app.state.notifier.emit("behavioral_drift", "agent-7",
                                      {"permalink": "https://cp/#/runs/7"}, org="org_acme")
    assert n == 1
    await app.state.notifier.drain()
    to, subject, body = outbox.sent[-1]
    assert to == "a@acme.io" and "agent-7" in subject and "drift" in subject
    assert "https://cp/#/runs/7" in body


async def test_someone_elses_address_is_refused(setup, priv) -> None:  # noqa: ANN001
    _, c = setup
    r = await c.post("/v1/notifications/subscribe",
                     headers=_bearer(_mint(priv, evf=True)),
                     json={**SUB, "url": "mailto:victim@example.com"})
    assert r.status_code == 403 and "your own address" in r.text


async def test_an_unconfirmed_address_is_refused(setup, priv) -> None:  # noqa: ANN001
    _, c = setup
    for token in (_mint(priv), _mint(priv, evf=False)):  # older token: no claim
        r = await c.post("/v1/notifications/subscribe", headers=_bearer(token),
                         json={**SUB, "url": "mailto:a@acme.io"})
        assert r.status_code == 403 and "confirm" in r.text


async def test_the_operator_may_name_any_address(setup) -> None:  # noqa: ANN001
    _, c = setup
    r = await c.post("/v1/notifications/subscribe", headers=_bearer(TOKEN),
                     json={**SUB, "url": "mailto:oncall@example.com"})
    assert r.status_code == 200, r.text


async def test_no_mail_configured_refuses_mail_subscriptions(setup) -> None:  # noqa: ANN001
    app, c = setup
    app.state.notifier._mailer = None
    r = await c.post("/v1/notifications/subscribe", headers=_bearer(TOKEN),
                     json={**SUB, "url": "mailto:oncall@example.com"})
    assert r.status_code == 400 and "does not send mail" in r.text


async def test_a_failed_send_is_dead_lettered(setup) -> None:  # noqa: ANN001
    app, c = setup

    class Broken:
        async def send(self, to: str, subject: str, body: str) -> None:
            raise OSError("connection refused")

    app.state.notifier._mailer = Broken()
    await c.post("/v1/notifications/subscribe", headers=_bearer(TOKEN),
                 json={**SUB, "url": "mailto:oncall@example.com"})
    await app.state.notifier.emit("behavioral_drift", "n1", {}, org="public")
    await app.state.notifier.drain()
    letters = app.state.notifier.dead_letters
    assert letters and "connection refused" in letters[-1].error


def test_addresses_and_transports() -> None:
    assert mail_address("https://hooks.example.com") is None
    assert mail_address("mailto:Ops%40Acme.io") == "ops@acme.io"
    for bad in ("mailto:", "mailto:a@b.io,c@d.io", "mailto:<a@b.io>", "mailto:a b@c.io"):
        with pytest.raises(ValueError):
            mail_address(bad)
    assert mailer_from_url(None) is None
    with pytest.raises(ValueError):
        mailer_from_url("http://mail")
    subject, _ = render({"trigger": "node_stale", "node_id": "x"})
    assert subject == "[Axor] x: agent went quiet"
