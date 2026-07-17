# Releasing the platform packages

`axor-proxy` and `axor-backend` publish to PyPI, and the compose images to
GHCR, from `.github/workflows/release.yml` when you push a `vX.Y.Z` tag.
Publishing is credential-free: PyPI via **Trusted Publishing (OIDC)**, GHCR via
the workflow's own `GITHUB_TOKEN`. No API token ever lives in the repo.

This runbook is the one-time setup plus the per-release procedure. It exists
because the launch success criterion is concrete: **`uvx axor-proxy --demo`
works on a clean machine.**

## Prerequisites (already true)

- Ecosystem deps are on PyPI: `axor-core` (≥0.9.2) and `axor-eval` (≥0.1.0).
  The two platform packages depend on them by version range (not git ref), so a
  fresh `pip install axor-proxy` resolves everything from PyPI. Verified in the
  clean-room check below.
- Versions are in lockstep: `packages/axor-proxy/pyproject.toml`,
  `packages/axor-backend/pyproject.toml`, and the root are all `0.1.0`. The tag
  you push must match (`v0.1.0`).

## One-time setup

### 1. Create the `release` GitHub environment

`release.yml`'s `pypi` job runs in `environment: release`. In the repo:
**Settings → Environments → New environment → `release`**. Optionally add a
required reviewer so a tag can't publish without a human approving the run.

### 2. Register the Trusted Publisher on PyPI — once per package

For **each** of `axor-proxy` and `axor-backend`, on pypi.org:

- If the project does not exist yet, use **Publishing → Add a pending
  publisher** (creates the project on first publish). If it exists, open the
  project's **Settings → Publishing → Add a new publisher**.
- Fill in exactly:

  | Field | Value |
  |---|---|
  | PyPI Project Name | `axor-proxy` (then repeat for `axor-backend`) |
  | Owner | `Bucha11` |
  | Repository name | `axor-control-plane` |
  | Workflow name | `release.yml` |
  | Environment name | `release` |

The `Workflow name` is the filename, not the `name:` field — it must be
`release.yml`. The `Environment name` must match step 1 (`release`) or PyPI
rejects the OIDC token.

Do the same on **test.pypi.org** first if you want a dry run (see below).

## Cut a release

```bash
# 1. Bump the version in all three pyprojects if this isn't 0.1.0.
#    (root, packages/axor-proxy, packages/axor-backend — keep them equal.)

# 2. Make sure main is green and the lockfile is current.
uv sync --all-packages
uv run pytest -q
uv run ruff check .

# 3. Tag and push. The tag drives the workflow.
git tag v0.1.0
git push origin v0.1.0
```

The `Release` workflow then: builds sdists+wheels for both packages, publishes
to PyPI via OIDC, and pushes `axor-platform` / `axor-frontend` images to GHCR.
If you added a required reviewer to the `release` environment, approve the run.

`workflow_dispatch` is also enabled, so you can re-run the publish from the
Actions tab without moving the tag (useful if GHCR succeeds but PyPI needs a
retry).

## Verify on a clean machine (the launch criterion)

The exact check, reproducible anywhere with `uv`:

```bash
uv venv cleanroom --python 3.12
VIRTUAL_ENV=cleanroom uv pip install axor-proxy      # pulls axor-core + axor-eval from PyPI
./cleanroom/bin/axor-proxy --demo --port 8477 &      # boots the observe-only demo proxy
curl -fsS http://127.0.0.1:8477/axor/healthz         # → {"ok":true,"armed":false}
```

Or the one-shot form the docs and UI quote:

```bash
uvx axor-proxy --demo
```

This repo's build was validated this way before tagging: both wheels build with
`uv build --package …`, install cleanly against PyPI-resolved ecosystem deps
(axor-core 0.9.2, axor-eval 0.1.0), the `axor-proxy` console script resolves,
and `--demo` serves a healthy endpoint. The `axor-backend` wheel ships its
`ee/` subtree (with its own LICENSE) and all Alembic migrations, which run
programmatically from the packaged `migrations/` directory (no external
`alembic.ini` needed).

## Dry run on TestPyPI (optional)

To rehearse without touching the real index, register the same trusted
publishers on **test.pypi.org**, add a `repository-url:
https://test.pypi.org/legacy/` to the publish step on a throwaway branch, push a
`v0.0.0-rcN` tag, then `uv pip install --index-url
https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/
axor-proxy`. Remove the override before the real release.
