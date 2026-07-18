# Contributing

Thanks for looking. Ground rules keep reviews fast:

- **Setup**: `uv sync --all-packages`, `cd frontend && pnpm i`. Run everything:
  `uv run pytest` (unit + backend E2E) and `cd frontend && pnpm e2e`.
- **Rule 0**: replay/gates are `axor_core.kernel`'s — the platform imports,
  never reimplements. A PR that duplicates kernel semantics will be asked to
  import instead.
- **Honesty rule**: no silent failure paths. Errors surface (4xx with a
  reason, dead-letter, warning log) or don't merge.
- **Tests**: behaviour changes come with a test that fails without the change.
  E2E for anything a user sees; lint (`uv run ruff check .`) and `tsc` clean.
- **Schema changes**: new alembic revision — never edit `0001_baseline.py`.
- **Naming**: "control plane" is both this platform and one of its subsystems
  (the SSE+POST advisory channel). In code and docs the subsystem is always
  the **plane service** (`axor_backend.plane`); "control plane" unqualified
  means the platform.
- **Security issues**: never as public issues — see SECURITY.md.
- **License**: Apache-2.0 (except `axor_backend/ee/`); contributions are
  accepted under the repo license. EE contributions need a maintainer thumbs-up
  *before* you write code.

Small PRs merge fast. Big ideas: open a Discussion first.
