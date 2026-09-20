"""A stranger who clones this repository can bring the stack up.

That is the whole claim, and it was false for months. The ecosystem deps
(`axor-core`, `axor-eval`, `axor-probe`, `axor-sentinel`, `axor-wrap`) were
private git refs once; when they moved to PyPI the *resolution* followed —
`uv.lock` has held zero git sources since — but the plumbing that had
authorised those fetches stayed exactly where it was. The Dockerfile mounted a
`github_token` BuildKit secret, compose declared it and passed it to three
services, `.env.example` asked for a PAT with `repo` scope, and the README's
first command was `GITHUB_TOKEN=ghp_… docker compose up --build`.

So the quickstart of a public, Apache-2.0 product demanded a credential that
only the owner could issue, to unlock dependencies that had been public for
several releases. Nobody hit it, because everybody who ran it already had the
token exported. It is the kind of failure no unit test catches and no
maintainer reproduces — which is why it is pinned here, at the level of the
files a newcomer actually touches, rather than trusted to a comment.

The rule this enforces: **nothing on the path from `git clone` to a running
stack may require an account, a token, or a login.** Publishing an image is
the alternative offered to a newcomer, never the excuse for a build that
cannot run without a secret.
"""
from __future__ import annotations

import pathlib
import re
import tomllib

# tests/ → axor-backend/ → packages/ → the repository root
ROOT = pathlib.Path(__file__).resolve().parents[3]

# Words that only ever appear when something is asking for a credential to
# fetch dependencies. `secrets.GITHUB_TOKEN` in a workflow is excluded by
# construction: it is GitHub's own automatic token, and the files that may use
# it are checked with a narrower rule below.
_CREDENTIAL = re.compile(
    r"AXOR_ECOSYSTEM_TOKEN|github_token|GITHUB_TOKEN|x-access-token|insteadOf"
)


def _read(rel: str) -> str:
    return (ROOT / rel).read_text("utf-8")


def _code(body: str) -> str:
    """Only prose may still name the credential — that is how the removal stays
    explained to the next person who wonders why there is no token here."""
    return "\n".join(
        line for line in body.splitlines() if not line.lstrip().startswith("#")
    )


def test_the_lockfile_resolves_every_dependency_from_an_index() -> None:
    """One git source in the lock and the build needs credentials again."""
    lock = _read("uv.lock")
    git_sources = re.findall(r"^source = \{ git = .*$", lock, re.M)
    assert not git_sources, (
        f"uv.lock resolves from git: {git_sources}. Every dependency must come "
        f"from a public index, or the quickstart needs a token again."
    )
    assert "git+http" not in lock


def test_no_package_pins_a_dependency_to_a_repository() -> None:
    """The lock is generated; `[tool.uv.sources]` is what generates it."""
    offenders: dict[str, list[str]] = {}
    for path in [ROOT / "pyproject.toml", *ROOT.glob("packages/*/pyproject.toml")]:
        sources = tomllib.loads(path.read_text("utf-8")).get("tool", {}).get(
            "uv", {}
        ).get("sources", {})
        named = [
            name
            for name, spec in sources.items()
            if isinstance(spec, dict) and not spec.get("workspace")
        ]
        if named:
            offenders[path.relative_to(ROOT).as_posix()] = sorted(named)
    assert not offenders, (
        f"dependencies pinned to something other than a workspace member or an "
        f"index: {offenders}. Publish it instead."
    )


def test_the_image_builds_without_a_secret() -> None:
    for rel in ("Dockerfile", "frontend/Dockerfile"):
        code = _code(_read(rel))
        assert "--mount=type=secret" not in code, (
            f"{rel} mounts a build secret; a newcomer cannot supply one."
        )
        found = sorted(set(_CREDENTIAL.findall(code)))
        assert not found, f"{rel} still reaches for a credential: {found}"


def test_compose_declares_no_secret_and_needs_no_login() -> None:
    code = _code(_read("docker-compose.yml"))
    found = sorted(set(_CREDENTIAL.findall(code)))
    assert not found, f"docker-compose.yml still wires a credential: {found}"
    assert not re.search(r"^secrets:", code, re.M), (
        "docker-compose.yml declares a top-level `secrets:` block again."
    )


def test_env_example_asks_for_no_token_to_build() -> None:
    code = _code(_read(".env.example"))
    found = sorted(set(_CREDENTIAL.findall(code)))
    assert not found, f".env.example asks an operator for: {found}"


def test_the_readme_quickstart_runs_as_written() -> None:
    """The command a newcomer copies is the one thing nobody re-reads."""
    body = _read("README.md")
    fenced = re.findall(r"```(.*?)```", body, re.S)
    offenders = [
        block.strip()
        for block in fenced
        if "docker compose" in block and _CREDENTIAL.search(block)
    ]
    assert not offenders, (
        f"the README's compose quickstart prefixes a credential: {offenders}"
    )


def test_ci_does_not_hand_the_build_a_token() -> None:
    """CI passing a token is how a build that needs one stays green."""
    for rel in (".github/workflows/ci.yml", ".github/workflows/release.yml"):
        code = _code(_read(rel))
        assert "AXOR_ECOSYSTEM_TOKEN" not in code, (
            f"{rel} still offers the ecosystem PAT to a build step."
        )
        assert "github_token=" not in code, (
            f"{rel} still passes a `github_token` build secret."
        )
