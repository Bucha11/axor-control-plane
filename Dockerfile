# Shared Python image for backend + identity + proxy — one build of the uv
# workspace, three entrypoints (docker-compose picks the command per service).
#
#   docker compose up --build
#
# No token, no secret, no account. Every ecosystem dependency this image needs
# — axor-core, axor-eval, axor-probe, axor-sentinel, axor-wrap — is a published
# PyPI release (see the root pyproject's [tool.uv.sources]), so the build
# resolves from the index like any other Python project.
#
# It used to mount a `github_token` BuildKit secret and rewrite git URLs with
# it, because the ecosystem deps were once private git refs. They stopped being
# that several releases ago; the plumbing stayed, and with it the only thing
# standing between a stranger who cloned a public, Apache-2.0 repository and a
# running stack: a quickstart whose first command asked for a credential the
# build no longer spent. Nothing here may reintroduce that — a dependency that
# cannot be installed from a public index does not belong in this image.
FROM python:3.12-slim AS base

# python:3.12-slim already carries ca-certificates; nothing here fetches over
# git, so neither git nor an apt layer is needed.
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /bin/uv

WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_COMPILE_BYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

# Only the metadata + sources uv needs to resolve the workspace — uv.lock
# included. Without the lock the image re-resolved every dependency at build
# time, so two builds of the same commit could ship different versions of the
# kernel that decides every verdict in this product.
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY scripts ./scripts

# `--locked` fails the build when uv.lock does not already satisfy the
# workspace, instead of quietly resolving something else and shipping it.
RUN uv sync --locked --all-packages --no-dev

LABEL org.opencontainers.image.source="https://github.com/Bucha11/axor-control-plane" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.description="Axor platform image: backend, identity service and observe-only proxy."

EXPOSE 8400 8401 8402
# Default to the backend; compose overrides `command` for identity and proxy.
CMD ["uvicorn", "axor_backend.main:app", "--factory", "--host", "0.0.0.0", "--port", "8400"]
