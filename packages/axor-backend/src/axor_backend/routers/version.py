"""What is actually deployed here.

Separate from ``/v1/healthz`` on purpose. That route is open and deliberately
says nothing but ``ok``: a health probe is the one endpoint guaranteed to be
reachable, and one that leaked a version would hand an anonymous scanner a list
of which advisories to try. This one is authenticated like any other read, so
the answer goes to people who are already inside.

The versions are read from the installed distributions rather than from a
constant, so they describe the process answering the request — not what someone
meant to deploy. An operator comparing this against the tag they rolled is the
whole point: if they disagree, the deploy did not take.
"""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version

from fastapi import APIRouter

router = APIRouter(prefix="/v1", tags=["version"])

# The platform package and the ecosystem it runs on. axor-core first: it is the
# kernel every verdict comes from, so it is the version that decides what this
# deployment DOES, more than the backend's own.
_PACKAGES = (
    "axor-core",
    "axor-backend",
    "axor-proxy",
    "axor-eval",
    "axor-wrap",
    "axor-sentinel",
    "axor-probe",
)


def _installed(name: str) -> str | None:
    try:
        return _dist_version(name)
    except PackageNotFoundError:
        # Not every deployment installs every optional package; absent is a fact
        # about this install, not an error, and null says so without guessing.
        return None


@router.get("/version")
async def version() -> dict:
    """Versions of the packages in THIS process.

    `axor-proxy` is reported when the image happens to ship it, but the proxy
    runs in its own container and answers no version route of its own, so a
    value here is the backend image's copy — not proof of what the proxy
    container is running. In the stock deploy both come from one image tag.
    """
    return {"versions": {name: _installed(name) for name in _PACKAGES}}
