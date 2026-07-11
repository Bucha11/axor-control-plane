# Shared Python image for backend + proxy — one build of the uv workspace, two
# entrypoints (docker-compose picks the command per service). The ecosystem deps
# axor-core / axor-eval are private git repos (one-way dependency: the platform
# imports them, never the reverse), so the build fetches them with a GitHub
# token supplied as a BuildKit secret:
#
#   GITHUB_TOKEN=ghp_xxx docker compose up --build
#
# (compose passes it through as the `github_token` secret — see compose.yaml).
# In an environment that can already reach the repos (a proxy with git redirect,
# or once axor-core is published to an index) the secret is optional.
FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /bin/uv

WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_COMPILE_BYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

# Only the metadata + sources uv needs to resolve the workspace.
COPY pyproject.toml ./
COPY packages ./packages
COPY scripts ./scripts

# Resolve + install the whole workspace (kernel-consuming backend + proxy). The
# token is written to a throwaway gitconfig and deleted in the SAME layer, so it
# never appears in any image layer diff — the secret mount is build-only too.
RUN --mount=type=secret,id=github_token sh -eu -c '\
    if [ -s /run/secrets/github_token ]; then \
      git config --global url."https://x-access-token:$(cat /run/secrets/github_token)@github.com/".insteadOf "https://github.com/"; \
    fi; \
    uv sync --all-packages --no-dev; \
    rm -f /root/.gitconfig'

EXPOSE 8400 8401
# Default to the backend; compose overrides `command` for the proxy service.
CMD ["uvicorn", "axor_backend.main:app", "--factory", "--host", "0.0.0.0", "--port", "8400"]
