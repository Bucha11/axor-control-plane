"""What is deployed, and who may ask.

The version surface exists so an operator can check that the tag they rolled is
the tag that is running. It is deliberately NOT part of /v1/healthz: that route
is open, and a version on the one endpoint guaranteed to be reachable is a list
of advisories handed to an anonymous scanner.
"""
from __future__ import annotations

import pathlib

import httpx
import pytest
from axor_backend.app import create_app

TOKEN = "master-secret-xyz"


def _client(app) -> httpx.AsyncClient:  # noqa: ANN001
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://backend.test")


@pytest.fixture
async def secured(tmp_path: pathlib.Path) -> httpx.AsyncClient:
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/v.db",
                     operator_keys={}, allow_unsigned=True, api_token=TOKEN)
    async with _client(app) as c, app.router.lifespan_context(app):
        yield c


async def test_version_reports_the_kernel_actually_installed(
        secured: httpx.AsyncClient) -> None:
    """Read from the installed distribution, not a constant: the answer must
    describe the process serving the request, so that a version matching the
    rolled tag is evidence the deploy took."""
    import axor_core

    r = await secured.get("/v1/version", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200
    versions = r.json()["versions"]
    assert versions["axor-core"] == axor_core.__version__
    assert versions["axor-backend"]


async def test_version_needs_a_credential(secured: httpx.AsyncClient) -> None:
    assert (await secured.get("/v1/version")).status_code == 401


async def test_healthz_still_says_nothing(secured: httpx.AsyncClient) -> None:
    """The open route stays mute. If a version ever appears here, the reasoning
    in routers/health.py was overruled by accident rather than on purpose."""
    r = await secured.get("/v1/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


async def test_an_absent_optional_package_is_null_not_a_guess(
        secured: httpx.AsyncClient) -> None:
    """Not every install carries every ecosystem package. Absent is a fact about
    this deployment; null reports it without inventing a version."""
    r = await secured.get("/v1/version", headers={"Authorization": f"Bearer {TOKEN}"})
    versions = r.json()["versions"]
    assert set(versions) >= {"axor-core", "axor-backend", "axor-proxy"}
    for name, v in versions.items():
        assert v is None or isinstance(v, str), name
