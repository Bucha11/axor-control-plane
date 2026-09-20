"""Every setting the backend reads is named in one place and documented in one.

`config.py` opens with two reasons for existing, and the second is that "a
setting nobody can enumerate cannot be documented" — with `.env.example`
maintained by memory as the thing that went wrong. The claim had come apart
again by the time this was written: ten of twenty-three variables were read
outside `AppConfig`, and twelve were missing from `.env.example`, including
`AXOR_DATABASE_URL` and `AXOR_LICENSE`.

A comment cannot hold that line. This can.
"""
from __future__ import annotations

import pathlib
import re

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "axor_backend"
# tests/ → axor-backend/ → packages/ → the repository root
ROOT = pathlib.Path(__file__).resolve().parents[3]
ENV_EXAMPLE = ROOT / ".env.example"

# Any AXOR_*/SENTRY_DSN string literal in the backend source. Catches both a
# direct `os.environ.get("AXOR_X")` and a name handed to `env.flag` / friends.
_LITERAL = re.compile(r'["\'](AXOR_[A-Z0-9_]+|SENTRY_DSN)["\']')
# `NAME=` at the start of a line in .env.example, commented out or not.
_DECLARED = re.compile(r"^#?\s*([A-Z][A-Z0-9_]*)=", re.M)

# Modules that read their own settings, on purpose, with the reason. Anything
# else reading the environment belongs in AppConfig — that is what "resolved
# once, at startup, into one frozen object" means.
READ_OUTSIDE_CONFIG: dict[str, str] = {
    "limits.py": (
        "ceilings the plane router needs; importing app from there is a cycle"
    ),
    "monitor.py": "the stale sweep's cadence, read where the task is spawned",
    "observability.py": "logging + Sentry, set up before the config exists",
    "ee/cli.py": "an operator CLI, not the server",
}


def _literals_by_file() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        names = set(_LITERAL.findall(path.read_text("utf-8")))
        if names:
            out[path.relative_to(SRC).as_posix()] = names
    return out


def _reads_the_environment(name: str) -> bool:
    """Naming a variable is not reading it: `lifecycle` names both vault tokens
    in the warning it prints when they are unset, and `notifications` keeps the
    block-private variable's name for its message. Only a module that actually
    reaches for the environment is one that bypassed AppConfig."""
    body = (SRC / name).read_text("utf-8")
    return "os.environ" in body or "import env" in body


def test_only_the_listed_modules_read_their_own_settings() -> None:
    strays = {
        name: sorted(names)
        for name, names in _literals_by_file().items()
        if name not in {"config.py", "env.py", *READ_OUTSIDE_CONFIG}
        and _reads_the_environment(name)
    }
    assert not strays, (
        f"these modules read settings that belong in AppConfig: {strays}. "
        f"Add the field to config.py, or list the module in "
        f"READ_OUTSIDE_CONFIG with the reason it cannot."
    )


def test_every_setting_the_backend_reads_is_in_env_example() -> None:
    read = {name for names in _literals_by_file().values() for name in names}
    documented = set(_DECLARED.findall(ENV_EXAMPLE.read_text("utf-8")))
    missing = sorted(read - documented)
    assert not missing, (
        f"read by the backend and absent from .env.example: {missing}. "
        f"This file exists so that list is always empty."
    )


def test_env_example_does_not_document_settings_nothing_reads() -> None:
    """The other direction, scoped to what this repo's backend owns: a variable
    this file explains and nothing consumes is a promise to an operator that
    nothing keeps. Proxy- and frontend-side variables are out of scope here."""
    backend = {name for names in _literals_by_file().values() for name in names}
    documented = set(_DECLARED.findall(ENV_EXAMPLE.read_text("utf-8")))
    orphans = sorted(documented - backend - _ELSEWHERE)
    assert not orphans, (
        f".env.example documents settings nothing reads: {orphans}"
    )


# Consumed by the proxy, the frontend's nginx, compose or the build rather than
# by the backend. The names are checked against those sources below, because a
# hand-kept exemption list is the same promise-nobody-keeps this file exists to
# catch — one name added here on a wrong assumption and the guard is off for it
# forever.
_ELSEWHERE = frozenset({
    "AXOR_PG_PASSWORD", "AXOR_INGEST_KEY", "AXOR_PROXY_DEMO",
    "AXOR_PROXY_TOKEN", "AXOR_VAULT_TOOLS", "AXOR_VAULT_ALLOW_REMOTE",
    "AXOR_NODE_SIGNING_SEED", "AXOR_CRED_SEALING_SEED",
    "AXOR_IDENTITY_SIGNING_KEY", "VITE_CHECKOUT_URL", "AXOR_TRACE_DIR",
    "AXOR_BACKEND_URL",
    # Which images compose runs, for the build-free path. GITHUB_TOKEN used to
    # sit in this list: a build-time credential for private git deps that had
    # been published to PyPI releases earlier, exempted here and therefore never
    # questioned again. See test_quickstart_is_credential_free.py.
    "AXOR_TAG", "AXOR_IMAGE", "AXOR_FRONTEND_IMAGE",
})


def test_the_elsewhere_list_is_true() -> None:
    """Every name exempted above is read by something in this repository."""
    sources = "\n".join(
        path.read_text("utf-8")
        for path in [
            *(ROOT / "packages/axor-proxy/src").rglob("*.py"),
            *(ROOT / "frontend/src").rglob("*.ts"),
            *(ROOT / "frontend").glob("*.conf"),
            ROOT / "docker-compose.yml",
            ROOT / "Dockerfile",
        ]
        if path.is_file()
    )
    unread = sorted(name for name in _ELSEWHERE if name not in sources)
    assert not unread, (
        f"exempted as 'consumed elsewhere' and consumed nowhere: {unread}"
    )
