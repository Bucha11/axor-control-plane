# Releasing the platform packages

`axor-proxy` and `axor-backend` publish to PyPI, and the compose images to
GHCR, from `.github/workflows/release.yml` when you push a `vX.Y.Z` tag. A push
to main publishes the images alone, as `:edge`.
Publishing is credential-free: PyPI via **Trusted Publishing (OIDC)**, GHCR via
the workflow's own `GITHUB_TOKEN`. No API token ever lives in the repo.

This runbook is the one-time setup plus the per-release procedure. It exists
because the launch success criterion is concrete: **`uvx axor-proxy --demo`
works on a clean machine.**

## Prerequisites (already true)

- Ecosystem deps are on PyPI: `axor-core`, `axor-eval`, `axor-probe`,
  `axor-sentinel` and `axor-wrap`. Every package depends on them by version
  range (not git ref), so a fresh `pip install axor-proxy` — and the Docker
  build — resolves everything from the public index. No PAT is involved
  anywhere: not in CI, not in the Dockerfile, not in `.env.example`. Verified
  in the clean-room check below and enforced by
  `test_quickstart_is_credential_free.py`.
- Versions are in lockstep: `packages/axor-proxy`, `packages/axor-backend`,
  `packages/axor-identity` and the root all carry the same version, and the tag
  you push must match it. The workflow checks the tag against `axor-proxy` and
  `axor-backend` and fails the release rather than publishing nothing; it does
  not check the other two, so keeping them in step is on you. (The root is a
  uv workspace container and is never published; `axor-identity` ships inside
  the `axor-platform` image, not to PyPI — see "What is published" below.)

## One-time setup

### 1. Create the two GitHub environments

The `pypi` job runs one matrix leg per package, each in its **own** environment:
`axor-proxy` → `pypi-proxy`, `axor-backend` → `pypi-backend`. This is required,
not cosmetic — PyPI keys a Trusted Publisher on (owner, repo, workflow-file,
environment), and a *pending* publisher config must be unique. Two packages
sharing one environment collide: the second registration is rejected with
"a pending trusted publisher matching this configuration has already been
registered for a different project name."

In the repo: **Settings → Environments → New environment** → create both
`pypi-proxy` and `pypi-backend`. Optionally add a required reviewer to each so a
tag can't publish without a human approving the run.

### 2. Register the Trusted Publisher on PyPI — once per package

On pypi.org, **Publishing → Add a pending publisher** (creates the project on
first publish). Register **two**, identical except for the project name **and**
the environment:

  | Field | axor-proxy | axor-backend |
  |---|---|---|
  | PyPI Project Name | `axor-proxy` | `axor-backend` |
  | Owner | `Bucha11` | `Bucha11` |
  | Repository name | `axor-control-plane` | `axor-control-plane` |
  | Workflow name | `release.yml` | `release.yml` |
  | Environment name | `pypi-proxy` | `pypi-backend` |

`Workflow name` is the **filename** (`release.yml`), not the workflow's `name:`
field (`Release`). Each `Environment name` must match the matrix leg above
exactly, or PyPI rejects the OIDC token. Because the environments differ, the
two pending configs are distinct and neither collides.

Do the same on **test.pypi.org** first if you want a dry run (see below).

## What is published

| | Where | Checked against the tag |
|---|---|---|
| `axor-proxy` | PyPI | yes |
| `axor-backend` | PyPI | yes |
| `axor-identity` | the `axor-platform` image only | no — keep its version in step by hand |
| `axor-platform`, `axor-frontend` | GHCR (`:X.Y.Z`, `:latest`; `:edge` on every push to main) | — |

Whether `axor-identity` should also go to PyPI is open. It has no CLI anyone
installs on their own; a self-hosted install gets it from the image, and the
offline bundle carries the same image. Adding it is one matrix leg plus a
PyPI environment — do it when somebody needs to `pip install` it, not before.

## The order across repositories

The packages depend on each other by version range, so a wave is released
bottom-up. Each step is: merge its release PR, push the tag, wait for PyPI,
then start the next.

```
1. axor-core        everything compiles against the kernel
2. axor-eval · axor-probe · axor-sentinel      (independent of each other)
3. axor-wrap        needs core; the plane and Lab need it
4. axor-control-plane    needs core, eval, probe, sentinel, wrap
5. axor-lab         needs all of the above, and pins the kernel line
```

Two traps this order exists to avoid:

- **A widened pin released before what it points at.** The plane pins
  `axor-wrap[plane]>=0.4,<0.5`; tagging the plane before wrap 0.4.0 is on PyPI
  gives a release nobody can install.
- **A frozen fixture that outlives its kernel.** Lab's replay fixtures record
  the kernel build they were taken under, and the plane refuses to claim a
  reproduction under a different build — which is the point, and also why
  `packages/axor-backend/tests/test_lab_import_frozen.py` fails whenever core
  moves ahead of the recording. Re-record it in the Lab step of the wave, or
  the test stays deselected and stops being a check.

A release that touches only one repository skips the rest: check what its
dependents pin before deciding it is alone.

## Cut a release

```bash
# 1. Bump the version in all four pyprojects (root, axor-proxy, axor-backend,
#    axor-identity — keep them equal) and write the CHANGELOG entry. Both go
#    through a `release/X.Y.Z` branch and its PR, like the sibling repos.

# 2. Make sure main is green and the lockfile is current.
uv sync --all-packages
uv run pytest -q
uv run ruff check .

# 3. Tag and push. The tag drives the workflow.
git tag v0.1.0
git push origin v0.1.0
```

The `Release` workflow then: builds sdists+wheels for both packages (one matrix
leg each), publishes to PyPI via OIDC, and pushes `axor-platform` /
`axor-frontend` images to GHCR. If you added required reviewers to the
`pypi-proxy` / `pypi-backend` environments, approve each leg's run.

`workflow_dispatch` is also enabled, so you can re-run the publish from the
Actions tab without moving the tag (useful if GHCR succeeds but PyPI needs a
retry).

### The offline bundle (for air-gapped customers)

Once the images are on GHCR, build the archive an air-gapped install is
carried in, and put its digests in the release notes:

```bash
cd deploy/offline
./bundle.sh 0.1.0                 # add --with-lab for a bundle with Axor Lab
cat axor-offline-0.1.0/MANIFEST.txt   # the digests to publish
```

It holds no secrets, so it can be attached to the release or handed over
however the customer prefers. `deploy/offline/README.md` is the install side.

## The images (GHCR) — and the one setting that makes them usable

Two images are published from the same `Release` workflow:

| Image | What | Tags |
|---|---|---|
| `ghcr.io/bucha11/axor-platform` | the uv workspace — backend, identity, proxy | `X.Y.Z`, `latest` on a tag · `edge` on every push to main |
| `ghcr.io/bucha11/axor-frontend` | nginx serving the built SPA (the single origin) | same |

`docker-compose.yml` resolves `${AXOR_IMAGE}:${AXOR_TAG}` against these, so the
build-free path in the README —

```bash
docker compose pull && docker compose up -d --no-build
```

— is the published image and nothing else. `edge` exists so that path works
*between* releases: before it, a reader was pointed at a `latest` tag that no
tag had ever created.

**One-time, and easy to miss:** a package pushed by Actions starts **private**
even from a public repository. Until it is flipped, `docker compose pull` fails
for everyone who is not you with `denied` — the same wall this release removed
from the build, moved to the registry. After the first successful run:

> github.com/users/Bucha11/packages → `axor-platform` → Package settings →
> Danger Zone → **Change visibility → Public**. Repeat for `axor-frontend`.

Then verify as an outsider, with no GHCR login at all:

```bash
docker logout ghcr.io
docker pull ghcr.io/bucha11/axor-platform:edge    # must succeed anonymously
```

Both images build with no credentials of any kind (CI's `quickstart` job proves
it on every pull request), so anyone can also rebuild them from a clone instead
of trusting the registry.

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
