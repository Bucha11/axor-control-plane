"""System of record: append-only events + runs/desired/reported/facts/pins.

Postgres in production (asyncpg), SQLite (aiosqlite) for dev and tests — the
schema sticks to portable types. Events are append-only; desired state is
versioned LWW (the lattice semantics live in axor_core.kernel.state — the
backend persists and fans out, it does not interpret).
"""
from __future__ import annotations

import contextlib
import json
from collections.abc import Callable
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Float,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    UniqueConstraint,
    delete,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from axor_backend.errors import ConcurrentUpdate, StaleVersion
from axor_backend.tenancy import PUBLIC_ORG, current_org_id

metadata = MetaData()

# JSON payload columns: real JSONB on Postgres (indexable, queryable), portable
# JSON (stored as TEXT, auto-(de)serialised) on SQLite for dev/tests. One column
# type, both deploys — the backend stops treating governance payloads as opaque
# blobs the moment it runs on Postgres.
_JSON = JSON().with_variant(JSONB(), "postgresql")

# Tenant-scoped key space (migration 0009). An identifier is unique WITHIN an
# organization, never globally: migration 0007 added org_id and scoped every
# READ by it, but left the single-column primary keys in place, so one tenant
# could still take an id out from under another — and for a Lab handoff, whose
# pins are the deterministic `lab:{trace_id}`, two tenants importing the same
# package collide by construction. org_id leads every key so the index that
# serves the tenant filter is the primary key itself.
runs = Table(
    "runs", metadata,
    Column("run_id", String(64), nullable=False),
    Column("node_id", String(128), nullable=False),
    Column("scenario", String(128), nullable=False, default="custom"),
    Column("intervened", Boolean, nullable=False, default=False),
    Column("completed", Boolean, nullable=False, default=False),
    Column("evidence_json", _JSON, nullable=False, default=list),
    Column("created_ts", String(40), nullable=False),
    Column("org_id", String(64), nullable=False, server_default=PUBLIC_ORG),
    PrimaryKeyConstraint("org_id", "run_id"),
)

events = Table(
    "events", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String(64), nullable=False, index=True),
    Column("node_id", String(128), nullable=False, index=True),
    Column("seq", Integer, nullable=False),
    Column("kind", String(40), nullable=False),
    Column("line", _JSON, nullable=False),  # full kernel-schema JSON line
    Column("org_id", String(64), nullable=False, server_default=PUBLIC_ORG, index=True),
    # Per-node sequences (spec v2 Ch.4 §5): seq is monotonic PER NODE, so a
    # multi-node run legitimately repeats seq across nodes.
    UniqueConstraint(
        "org_id", "run_id", "node_id", "seq", name="uq_events_run_node_seq"
    ),
)

# Idempotency keys are client-chosen, so they are scoped per tenant too: a
# collision across organizations would silently drop the other tenant's batch
# (ingest_events returns 0 stored), which is a denial of ingest disguised as a
# successful dedupe.
ingest_keys = Table(
    "ingest_keys", metadata,
    Column("key", String(128), nullable=False),
    Column("org_id", String(64), nullable=False, server_default=PUBLIC_ORG),
    PrimaryKeyConstraint("org_id", "key"),
)

desired_state = Table(
    "desired_state", metadata,
    Column("node_id", String(128), nullable=False),
    Column("version", Integer, nullable=False),
    Column("state_json", _JSON, nullable=False),
    Column("org_id", String(64), nullable=False, server_default=PUBLIC_ORG),
    PrimaryKeyConstraint("org_id", "node_id"),
)

reported_state = Table(
    "reported_state", metadata,
    Column("node_id", String(128), nullable=False),
    Column("applied_version", Integer, nullable=False, default=0),
    Column("level", String(24), nullable=False, default="NORMAL"),
    Column("budget_remaining", Integer, nullable=True),
    Column("updated_ts", String(40), nullable=False),
    Column("org_id", String(64), nullable=False, server_default=PUBLIC_ORG),
    PrimaryKeyConstraint("org_id", "node_id"),
)

facts = Table(
    "facts", metadata,
    Column("fact_id", String(128), nullable=False),
    Column("node_id", String(128), nullable=False, index=True),
    Column("fact_json", _JSON, nullable=False),
    Column("created_ts", String(40), nullable=False),
    Column("org_id", String(64), nullable=False, server_default=PUBLIC_ORG),
    PrimaryKeyConstraint("org_id", "fact_id"),
)

pins = Table(
    "pins", metadata,
    Column("run_id", String(64), nullable=False),
    Column("side", String(16), nullable=False),  # must_block | must_pass
    Column("label", String(200), nullable=False, default=""),
    Column("org_id", String(64), nullable=False, server_default=PUBLIC_ORG),
    PrimaryKeyConstraint("org_id", "run_id"),
)

# Share links & notification subscriptions are PRIMARY user data (a revocable
# permalink; a webhook the on-call registered), not a derived index like the
# taint graph — so they persist here and rehydrate into their in-memory holders
# at boot. Without this a restart 404s every shared EvidenceCase and silently
# stops every notification.
# The token stays the sole primary key: it is an unguessable global capability,
# and `GET /v1/share/{token}` is served WITHOUT auth, so there is no principal
# to take an org from. The org_id column (migration 0009) is what lets that
# open route scope itself — it reads the link, adopts the link's tenant, and
# only then looks the case up (before 0009 the lookup ran under the public
# tenant and 404'd every link an identity user had created).
share_links = Table(
    "share_links", metadata,
    Column("token", String(64), primary_key=True),
    Column("run_id", String(64), nullable=False),
    Column("case_index", Integer, nullable=False),
    Column("revoked", Boolean, nullable=False, default=False),
    Column("created_ts", String(40), nullable=False),
    Column("org_id", String(64), nullable=False, server_default=PUBLIC_ORG, index=True),
)

notification_subs = Table(
    "notification_subs", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("url", String(500), nullable=False),
    Column("triggers", String(300), nullable=False),  # comma-separated, sorted
    Column("debounce_seconds", Float, nullable=False, default=0.0),
    # Routing (EE, migration 0003): a named channel + a node glob. The free
    # tier is one global webhook — label "" and pattern "*".
    Column("label", String(100), nullable=False, default="", server_default=""),
    Column("node_pattern", String(200), nullable=False, default="*", server_default="*"),
    Column("org_id", String(64), nullable=False, server_default=PUBLIC_ORG),
    # org_id joins the uniqueness (migration 0010): two tenants may register the
    # same URL with the same triggers, and neither may dedupe the other away.
    UniqueConstraint(
        "org_id", "url", "triggers", "node_pattern", name="uq_sub_url_triggers"
    ),
)

# Small KV for operator-set runtime state that must survive restarts: the
# active EE license, the regression schedule, and BOTH federation vaults —
# enrolled tool credentials, signing-key seeds and the signing audit log. That
# last part is why this table is org-scoped (migration 0009) rather than left
# global as 0007 had it: a global KV means one tenant's `get_setting` returns
# another tenant's vault.
settings = Table(
    "settings", metadata,
    Column("key", String(64), nullable=False),
    Column("value", _JSON, nullable=False),
    Column("org_id", String(64), nullable=False, server_default=PUBLIC_ORG),
    PrimaryKeyConstraint("org_id", "key"),
)

# Every corpus run leaves a report (source: manual | scheduled) — the history
# an org needs to answer "when did this config last regress" (EE surfaces it).
regression_reports = Table(
    "regression_reports", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("created_ts", String(40), nullable=False),
    Column("source", String(16), nullable=False),  # manual | scheduled
    Column("regressed", Integer, nullable=False),
    Column("escaped", Integer, nullable=False),
    Column("skipped", Integer, nullable=False),
    Column("total", Integer, nullable=False),
    Column("safe_to_ship", Boolean, nullable=False),
    Column("report_json", _JSON, nullable=False),
    Column("org_id", String(64), nullable=False, server_default=PUBLIC_ORG, index=True),
)

# Accepted Lab deploy packages (axor-cp-deploy/v1, migration 0005): the record
# of every finalized Lab handoff — validated policy + manifests stored verbatim
# (package_json), plus the summary columns the list surface reads. The pins the
# package created live in `pins` (labelled lab:{package_id}); this table is the
# provenance of where they came from.
lab_deploys = Table(
    "lab_deploys", metadata,
    Column("package_id", String(64), nullable=False),
    Column("created_ts", String(40), nullable=False),
    Column("kernel", String(120), nullable=False),
    Column("config_hash", String(80), nullable=False),
    Column("parametric_config_hash", String(80), nullable=False),
    Column("pins_created", Integer, nullable=False, default=0),
    Column("manifest_count", Integer, nullable=False, default=0),
    Column("package_json", _JSON, nullable=False),
    Column("org_id", String(64), nullable=False, server_default=PUBLIC_ORG),
    # package_id is a content hash of the package, so two tenants deploying the
    # same Lab export collide on it by construction.
    PrimaryKeyConstraint("org_id", "package_id"),
)

# Dead letters are the honesty ledger of the notification channel: a webhook
# that never arrived. They persist (capped) so a restart doesn't erase the
# evidence that deliveries were lost — the exact failure mode the dead-letter
# log exists to expose.
dead_letters = Table(
    "dead_letters", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("url", String(500), nullable=False),
    Column("payload_json", _JSON, nullable=False),
    Column("error", String(500), nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("created_ts", String(40), nullable=False),
    Column("org_id", String(64), nullable=False, server_default=PUBLIC_ORG, index=True),
)

# Behavioral health checks (axor-probe, migration 0006). One row per battery a
# node ran and posted; history is kept because the panel's drift sparkline only
# appears once ≥2 checks exist (ui-spec 8.2). This is DRIFT evidence and lives
# apart from the eval corpus on purpose: drift answers "has my agent changed?",
# eval answers "does my agent lie under fault?", and the two must never be
# blended into one score (ui-spec 8.2). The summary columns are what the list
# surface reads; payload_json is axor-probe's health_payload verbatim.
probe_reports = Table(
    "probe_reports", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("node_id", String(128), nullable=False, index=True),
    Column("created_ts", String(40), nullable=False),
    Column("session_id", String(128), nullable=False, default=""),
    Column("overall_verdict", String(32), nullable=False),
    Column("escape_count", Integer, nullable=False, default=0),
    Column("probes_sent", Integer, nullable=False, default=0),
    Column("payload_json", _JSON, nullable=False),
    Column("org_id", String(64), nullable=False, server_default=PUBLIC_ORG, index=True),
)

api_keys = Table(
    "api_keys", metadata,
    Column("key_id", String(32), primary_key=True),
    Column("hashed_secret", String(64), nullable=False),  # sha256 hex
    Column("scopes", String(200), nullable=False),        # comma-separated
    Column("label", String(200), nullable=False, default=""),
    Column("created_ts", String(40), nullable=False),
    Column("org_id", String(64), nullable=False, server_default=PUBLIC_ORG, index=True),
    # A key minted for ONE governed node names it here (migration 0008); the
    # plane then refuses to let that key speak for any other node. NULL = an
    # unbound fleet-wide operator key, the pre-0008 behaviour.
    Column("node_id", String(128), nullable=True),
)


class _NoSuchNode(Exception):
    """Internal: `_merge_desired` was asked to edit a node with no desired
    state. Only `clear_desired_key` can hit it, where it means the ack arrived
    for a node the plane never commanded — a no-op, not an error."""


def make_engine(url: str) -> AsyncEngine:
    return create_async_engine(url)


# Baseline revision (migrations/versions/0001_baseline.py). A database created
# by a pre-migration build has the tables but no alembic_version — stamp it as
# the baseline, then upgrade, so early adopters cross over without data loss.
_BASELINE_REV = "0001"


def _run_migrations(sync_conn: Any) -> None:  # noqa: ANN401 - sync Connection
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    cfg = Config()
    cfg.set_main_option(
        "script_location", str(Path(__file__).parent / "migrations")
    )
    cfg.attributes["connection"] = sync_conn

    inspector = inspect(sync_conn)
    tables = set(inspector.get_table_names())
    if "runs" in tables and "alembic_version" not in tables:
        command.stamp(cfg, _BASELINE_REV)
    command.upgrade(cfg, "head")


async def init_db(engine: AsyncEngine) -> None:
    """Bring the schema to head via alembic (launch-readiness §1): fresh DBs
    get the full baseline, legacy pre-migration DBs are stamped then upgraded,
    and future schema changes ship as new revisions instead of stranding
    early adopters on create_all."""
    async with engine.begin() as conn:
        await conn.run_sync(_run_migrations)


class Store:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    # ── runs & events ─────────────────────────────────────────────────────────

    async def upsert_run(self, run_id: str, node_id: str, scenario: str, ts: str) -> None:
        """Create the run if it is not there. Idempotent, and safe against a
        concurrent create of the same id: the composite primary key decides, and
        losing that race means the row exists, which is the goal."""
        try:
            async with self.engine.begin() as conn:
                existing = (
                    await conn.execute(select(runs.c.run_id).where(
                        runs.c.run_id == run_id,
                        runs.c.org_id == current_org_id(),
                    ))
                ).first()
                if existing is None:
                    await conn.execute(insert(runs).values(
                        run_id=run_id, node_id=node_id, scenario=scenario,
                        intervened=False, completed=False, evidence_json=[],
                        created_ts=ts, org_id=current_org_id(),
                    ))
        except IntegrityError:
            return  # another request created it between the select and the insert

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        """One run by id. Every caller that needs a single run used to build a
        dict from `list_runs()` and index into it — reading the whole table, with
        every EvidenceCase blob in it, to find one row. `GET /v1/share/{token}`
        is unauthenticated and did exactly that."""
        async with self.engine.connect() as conn:
            row = (await conn.execute(
                select(runs).where(
                    runs.c.run_id == run_id,
                    runs.c.org_id == current_org_id(),
                )
            )).first()
        return _run_row(row) if row is not None else None

    async def ingest_events(
        self, run_id: str, node_id: str, lines: list[dict[str, Any]],
        idempotency_key: str | None,
    ) -> list[tuple[int, dict[str, Any]]]:
        """Append a telemetry batch, returning the events actually stored as
        ``(event_id, line)`` — empty on a duplicate batch.

        The id matters to the caller: it is the cursor the live audit stream
        hands clients as the SSE ``id:`` field, and the only value a reconnect
        can resume from. Per-node ``seq`` cannot serve that role, because a
        multi-node run repeats it across nodes.

        Deduplication is on ``(node_id, seq)`` and covers duplicates WITHIN the
        batch as well as against what is already stored. It did not before, so a
        client that repeated a line inside one request got an IntegrityError and
        a 500 instead of the dedupe this method promises.
        """
        org = current_org_id()
        async with self.engine.begin() as conn:
            if idempotency_key:
                dup = (await conn.execute(
                    select(ingest_keys.c.key).where(
                        ingest_keys.c.key == idempotency_key,
                        ingest_keys.c.org_id == org,
                    )
                )).first()
                if dup is not None:
                    return []
                await conn.execute(insert(ingest_keys).values(
                    key=idempotency_key, org_id=org,
                ))
            seen = await _stored_coordinates(conn, run_id, org, lines, node_id)
            payload: list[dict[str, Any]] = []
            fresh: list[dict[str, Any]] = []
            for line in lines:
                # Multi-node runs carry per-line node identity (spec v2 Ch.4);
                # fall back to the batch's node for legacy lines.
                coord = (line.get("node_id", node_id), line["seq"])
                if coord in seen:
                    continue
                seen.add(coord)
                fresh.append(line)
                payload.append({
                    "run_id": run_id, "node_id": coord[0], "seq": coord[1],
                    "kind": line["kind"], "line": line, "org_id": org,
                })
            if not payload:
                return []
            # One statement, not one per event. At the 10k-per-request ceiling
            # the row-at-a-time loop took seconds of round-trips inside a single
            # transaction; RETURNING hands back the ids in insertion order.
            result = await conn.execute(insert(events).returning(events.c.id), payload)
            return list(zip((r.id for r in result), fresh, strict=True))

    async def run_events(self, run_id: str) -> list[str]:
        """The run's events in APPEND order — which is causal order.

        Ordering by ``seq`` was wrong for any multi-node run: seq is monotonic
        per node (see the events table), so ordering by it globally interleaves
        the nodes and breaks ties by whatever the index happens to yield. On the
        demo tree that placed a ``message_received`` five positions before the
        ``message_sent`` that caused it, and made the order differ between
        SQLite and Postgres — in a trace whose whole promise is that replay
        reproduces what was recorded.

        ``events.id`` is the order the events arrived in, which is the order
        they happened in.
        """
        async with self.engine.connect() as conn:
            rows = (await conn.execute(
                select(events.c.line)
                .where(
                    events.c.run_id == run_id,
                    events.c.org_id == current_org_id(),
                )
                .order_by(events.c.id)
            )).all()
        # The column is native JSON; callers of this method still expect the raw
        # kernel-schema line as a string, so re-serialise on the way out.
        return [json.dumps(r.line) for r in rows]

    async def run_events_after(
        self, run_id: str, after_id: int = 0,
    ) -> list[tuple[int, str]]:
        """``(event_id, line)`` after a cursor — the audit stream's replay.

        The cursor is the event id, not the seq, for the same reason the order
        is: ids are unique and monotonic across the whole run, seq is neither.
        A reconnect carrying ``Last-Event-ID`` resumes exactly where it stopped;
        with seq it silently dropped every event whose node happened to number
        it below the last one delivered.
        """
        async with self.engine.connect() as conn:
            rows = (await conn.execute(
                select(events.c.id, events.c.line)
                .where(
                    events.c.run_id == run_id,
                    events.c.id > after_id,
                    events.c.org_id == current_org_id(),
                )
                .order_by(events.c.id)
            )).all()
        return [(r.id, json.dumps(r.line)) for r in rows]

    async def add_lab_trace_events(
        self, run_id: str, lines: list[dict[str, Any]],
    ) -> int:
        """Store the kernel-schema events converted from a Lab regression pin's
        trace under ``run_id`` (``lab:{trace_id}``), so ``run_events`` returns
        them and the regression corpus replays the pin instead of skipping it.

        Idempotent on (run_id, node_id, seq) — a package re-upload re-asserts the
        same events without duplicating them, preserving the append-only events
        invariant. The stored lines are converted-from-Lab kernel events; their
        ``lab:`` run_id prefix marks the provenance (a Lab handoff, not plane
        telemetry ingested from a governed node)."""
        async with self.engine.begin() as conn:
            seen = {
                (row.node_id, row.seq) for row in (await conn.execute(
                    select(events.c.node_id, events.c.seq)
                    .where(
                        events.c.run_id == run_id,
                        events.c.org_id == current_org_id(),
                    )
                )).all()
            }
            stored = 0
            for line in lines:
                node_id = str(line.get("node_id", "root"))
                seq = int(line["seq"])
                if (node_id, seq) in seen:
                    continue
                await conn.execute(insert(events).values(
                    run_id=run_id, node_id=node_id, seq=seq,
                    kind=str(line["kind"]), line=line,
                    org_id=current_org_id(),
                ))
                seen.add((node_id, seq))
                stored += 1
            return stored

    async def list_runs(self) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            rows = (await conn.execute(
                select(runs).where(runs.c.org_id == current_org_id())
                .order_by(runs.c.created_ts.desc())
            )).all()
        return [_run_row(r) for r in rows]

    async def set_evidence(self, run_id: str, evidence: list[dict[str, Any]]) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                update(runs).where(
                    runs.c.run_id == run_id,
                    runs.c.org_id == current_org_id(),
                ).values(
                    evidence_json=evidence, completed=True,
                )
            )

    async def mark_intervened(self, run_id: str) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                update(runs).where(
                    runs.c.run_id == run_id,
                    runs.c.org_id == current_org_id(),
                ).values(intervened=True)
            )

    # ── desired / reported state ──────────────────────────────────────────────

    async def bump_desired(
        self, node_id: str, delta: dict[str, Any], *, expect_version: int | None = None,
    ) -> tuple[int, dict[str, Any]]:
        """Apply a declarative delta, assign the next version, persist, return
        (version, merged_state). LWW per key; `stopped` absorbing is enforced
        at the adapter — the backend stores what was commanded.

        The write is conditional on the version that was read. Without that
        condition this was a read-modify-write race on the governance command
        channel: two commands landing together both read version N, both wrote
        N+1, and one operator's delta vanished after the plane had already
        answered 202. A command that is acknowledged and never applied is the
        one failure this channel must not have.

        `expect_version` is how a SIGNED command uses it. The signature covers
        (node_id, version, delta, timestamp), so such a command may only land at
        the version it was signed for: passing the version the operator signed
        makes the store refuse rather than retry, and the operator re-signs. Left
        unset — an internal or unsigned bump — contention is simply retried.
        """
        return await self._merge_desired(
            node_id, lambda state: {**state, **delta}, expect_version=expect_version,
        )

    async def clear_desired_key(self, node_id: str, key: str) -> None:
        """Consumption ack (injection/excision): clear the one-shot from state.

        Same optimistic write as `bump_desired`, for the same reason: an ack
        racing a command must not resurrect the one-shot it just consumed, nor
        drop the command.
        """
        def without(state: dict[str, Any]) -> dict[str, Any]:
            rest = dict(state)
            rest.pop(key, None)
            return rest

        with contextlib.suppress(_NoSuchNode):
            await self._merge_desired(
                node_id, without, require_existing=True, skip_if_unchanged=True,
            )

    async def _merge_desired(
        self,
        node_id: str,
        merge: Callable[[dict[str, Any]], dict[str, Any]],
        *,
        require_existing: bool = False,
        skip_if_unchanged: bool = False,
        expect_version: int | None = None,
        attempts: int = 8,
    ) -> tuple[int, dict[str, Any]]:
        """Read the node's desired state, merge, and write it back only if
        nobody else moved the version meanwhile. Returns (version, state)."""
        org = current_org_id()
        if expect_version is not None:
            attempts = 1  # a signed command is valid for one version only
        for _ in range(attempts):
            try:
                async with self.engine.begin() as conn:
                    row = (await conn.execute(
                        select(desired_state).where(
                            desired_state.c.node_id == node_id,
                            desired_state.c.org_id == org,
                        )
                    )).first()
                    if row is None:
                        if require_existing:
                            raise _NoSuchNode(node_id)
                        if expect_version not in (None, 0):
                            raise StaleVersion(
                                f"node {node_id!r} has no desired state; "
                                f"expected version {expect_version}"
                            )
                        state = merge({})
                        await conn.execute(insert(desired_state).values(
                            node_id=node_id, version=1, state_json=state,
                            org_id=org,
                        ))
                        return 1, state
                    if expect_version is not None and row.version != expect_version:
                        raise StaleVersion(
                            f"node {node_id!r} is at version {row.version}, not "
                            f"{expect_version}; the command was signed for a "
                            f"state that has since moved"
                        )
                    state = merge(dict(row.state_json))
                    if skip_if_unchanged and state == row.state_json:
                        # A repeated consumption ack must not bump the version:
                        # the adapter would fetch a "new" desired state that is
                        # byte-identical to the one it already applied.
                        return row.version, dict(row.state_json)
                    version = row.version + 1
                    result = await conn.execute(
                        update(desired_state)
                        .where(
                            desired_state.c.node_id == node_id,
                            desired_state.c.org_id == org,
                            # The whole fix: this row must still be the one we
                            # read. Zero rows updated means we lost — re-read.
                            desired_state.c.version == row.version,
                        )
                        .values(version=version, state_json=state)
                    )
                    if result.rowcount:
                        return version, state
            except IntegrityError:
                continue  # lost the create race; the next pass merges instead
        raise ConcurrentUpdate(
            f"desired state for node {node_id!r} was changed concurrently "
            f"{attempts} times running; the command was not applied"
        )

    async def get_desired(self, node_id: str) -> tuple[int, dict[str, Any]] | None:
        async with self.engine.connect() as conn:
            row = (await conn.execute(
                select(desired_state).where(
                    desired_state.c.node_id == node_id,
                    desired_state.c.org_id == current_org_id(),
                )
            )).first()
        if row is None:
            return None
        return row.version, row.state_json

    async def upsert_reported(
        self, node_id: str, applied_version: int, level: str,
        budget_remaining: int | None, ts: str,
    ) -> None:
        async with self.engine.begin() as conn:
            row = (await conn.execute(
                select(reported_state.c.node_id)
                .where(
                    reported_state.c.node_id == node_id,
                    reported_state.c.org_id == current_org_id(),
                )
            )).first()
            values = {
                "applied_version": applied_version, "level": level,
                "budget_remaining": budget_remaining, "updated_ts": ts,
            }
            if row is None:
                await conn.execute(insert(reported_state).values(
                    node_id=node_id, org_id=current_org_id(), **values,
                ))
            else:
                await conn.execute(
                    update(reported_state)
                    .where(
                        reported_state.c.node_id == node_id,
                        reported_state.c.org_id == current_org_id(),
                    ).values(**values)
                )

    async def get_reported(self, node_id: str) -> dict[str, Any] | None:
        async with self.engine.connect() as conn:
            row = (await conn.execute(
                select(reported_state).where(
                    reported_state.c.node_id == node_id,
                    reported_state.c.org_id == current_org_id(),
                )
            )).first()
        if row is None:
            return None
        return {
            "applied_version": row.applied_version, "level": row.level,
            "budget_remaining": row.budget_remaining, "updated_ts": row.updated_ts,
        }

    async def list_reported(self) -> list[dict[str, Any]]:
        """Every node that has ever reported, with its last heartbeat ts — the
        input the stale monitor scans (spec §16: node_stale after 3T silence)."""
        async with self.engine.connect() as conn:
            rows = (await conn.execute(
                select(
                    reported_state.c.node_id, reported_state.c.level,
                    reported_state.c.updated_ts,
                ).where(reported_state.c.org_id == current_org_id())
            )).all()
        return [
            {"node_id": r.node_id, "level": r.level, "updated_ts": r.updated_ts}
            for r in rows
        ]

    async def topology_events(self) -> list[dict[str, Any]]:
        """All structure-bearing trace lines (spec v2 Ch.4 §6): spawn and
        message events, in ingest order. Topology derives from these only."""
        async with self.engine.connect() as conn:
            rows = (await conn.execute(
                select(events.c.line)
                .where(
                    events.c.kind.in_(
                        ("node_spawned", "message_sent", "message_received")
                    ),
                    events.c.org_id == current_org_id(),
                )
                .order_by(events.c.run_id, events.c.node_id, events.c.seq)
            )).all()
        return [r.line for r in rows]

    async def list_nodes(self) -> list[str]:
        async with self.engine.connect() as conn:
            desired = (await conn.execute(select(desired_state.c.node_id).where(
                desired_state.c.org_id == current_org_id()
            ))).all()
            reported = (await conn.execute(select(reported_state.c.node_id).where(
                reported_state.c.org_id == current_org_id()
            ))).all()
        return sorted({r.node_id for r in desired} | {r.node_id for r in reported})

    # ── facts ─────────────────────────────────────────────────────────────────

    async def append_fact(self, node_id: str, fact: dict[str, Any], ts: str) -> bool:
        """Append-only: a duplicate fact_id is refused, never replaced.

        The primary key is the real arbiter — two concurrent appends of one
        fact_id both pass the check, and the loser is refused here rather than
        raising, which is the same answer it would have got a millisecond later.
        """
        try:
            async with self.engine.begin() as conn:
                dup = (await conn.execute(
                    select(facts.c.fact_id).where(
                        facts.c.fact_id == fact["fact_id"],
                        facts.c.org_id == current_org_id(),
                    )
                )).first()
                if dup is not None:
                    return False
                await conn.execute(insert(facts).values(
                    fact_id=fact["fact_id"], node_id=node_id,
                    fact_json=fact, created_ts=ts, org_id=current_org_id(),
                ))
                return True
        except IntegrityError:
            return False

    async def node_facts(self, node_id: str) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            rows = (await conn.execute(
                select(facts.c.fact_json)
                .where(
                    facts.c.node_id == node_id,
                    facts.c.org_id == current_org_id(),
                )
                .order_by(facts.c.created_ts)
            )).all()
        return [r.fact_json for r in rows]

    async def all_facts(self) -> list[dict[str, Any]]:
        """Every fact across all nodes, oldest first — the graph rehydrator folds
        attestations from here (a fact can exist for a node with no plane state)."""
        async with self.engine.connect() as conn:
            rows = (await conn.execute(
                select(facts.c.fact_json)
                .where(facts.c.org_id == current_org_id())
                .order_by(facts.c.created_ts)
            )).all()
        return [r.fact_json for r in rows]

    # ── API keys (auth, architecture section 9) ───────────────────────────────

    async def create_api_key(
        self, key_id: str, hashed_secret: str, scopes: list[str],
        label: str, ts: str, org: str | None = None,
        node_id: str | None = None,
    ) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(insert(api_keys).values(
                key_id=key_id, hashed_secret=hashed_secret,
                scopes=",".join(scopes), label=label, created_ts=ts,
                org_id=org if org is not None else current_org_id(),
                node_id=node_id,
            ))

    async def get_api_key(self, key_id: str) -> dict[str, Any] | None:
        # Global lookup by key_id: auth resolves the key BEFORE the request's
        # org is known, so this is NOT org-scoped — it returns the key's org_id
        # so the caller can stamp the request.
        async with self.engine.connect() as conn:
            row = (await conn.execute(
                select(api_keys).where(api_keys.c.key_id == key_id)
            )).first()
        if row is None:
            return None
        return {
            "key_id": row.key_id, "hashed_secret": row.hashed_secret,
            "scopes": [s for s in row.scopes.split(",") if s],
            "label": row.label, "created_ts": row.created_ts,
            "org_id": row.org_id, "node_id": row.node_id,
        }

    async def list_api_keys(self) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            rows = (await conn.execute(
                select(api_keys).where(api_keys.c.org_id == current_org_id())
            )).all()
        return [
            {"key_id": r.key_id, "scopes": [s for s in r.scopes.split(",") if s],
             "label": r.label, "created_ts": r.created_ts, "node_id": r.node_id}
            for r in rows
        ]

    async def delete_api_key(self, key_id: str) -> bool:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                delete(api_keys).where(
                    api_keys.c.key_id == key_id,
                    api_keys.c.org_id == current_org_id(),
                )
            )
        return bool(result.rowcount)

    # ── pins (regression corpus, decision 11) ─────────────────────────────────

    async def pin(self, run_id: str, side: str, label: str = "") -> None:
        """Pin a run into the corpus, or move its side/label. Idempotent, and a
        concurrent first pin of the same run resolves to one row."""
        try:
            await self._pin(run_id, side, label)
        except IntegrityError:
            await self._pin(run_id, side, label)  # the row now exists: update it

    async def _pin(self, run_id: str, side: str, label: str) -> None:
        async with self.engine.begin() as conn:
            dup = (await conn.execute(
                select(pins.c.run_id).where(
                    pins.c.run_id == run_id,
                    pins.c.org_id == current_org_id(),
                )
            )).first()
            if dup is None:
                await conn.execute(insert(pins).values(
                    run_id=run_id, side=side, label=label,
                    org_id=current_org_id(),
                ))
            else:
                await conn.execute(
                    update(pins).where(
                        pins.c.run_id == run_id,
                        pins.c.org_id == current_org_id(),
                    )
                    .values(side=side, label=label)
                )

    async def prune_runs_older_than(self, cutoff_ts: str) -> int:
        """Retention (launch-readiness §1): delete runs created before the ISO
        cutoff, with their events, pins and share links. Plane state (facts,
        desired/reported) is node-scoped operational state and is kept.
        created_ts is ISO-8601, so lexicographic compare is chronological."""
        async with self.engine.begin() as conn:
            old_ids = [
                r.run_id for r in (await conn.execute(
                    select(runs.c.run_id).where(
                        runs.c.created_ts < cutoff_ts,
                        runs.c.org_id == current_org_id(),
                    )
                )).all()
            ]
            if not old_ids:
                return 0
            await conn.execute(delete(events).where(
                events.c.run_id.in_(old_ids),
                events.c.org_id == current_org_id(),
            ))
            await conn.execute(delete(pins).where(
                pins.c.run_id.in_(old_ids),
                pins.c.org_id == current_org_id(),
            ))
            await conn.execute(delete(share_links).where(
                share_links.c.run_id.in_(old_ids),
                share_links.c.org_id == current_org_id(),
            ))
            await conn.execute(delete(runs).where(
                runs.c.run_id.in_(old_ids),
                runs.c.org_id == current_org_id(),
            ))
            return len(old_ids)

    async def pinned(self) -> list[dict[str, str]]:
        async with self.engine.connect() as conn:
            rows = (await conn.execute(
                select(pins).where(pins.c.org_id == current_org_id())
            )).all()
        return [
            {"run_id": r.run_id, "side": r.side, "label": r.label} for r in rows
        ]

    # ── share links (EvidenceCase permalinks, spec §8.3) ──────────────────────

    async def create_share_link(
        self, token: str, run_id: str, case_index: int, ts: str,
    ) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(insert(share_links).values(
                token=token, run_id=run_id, case_index=case_index,
                revoked=False, created_ts=ts, org_id=current_org_id(),
            ))

    async def revoke_share_link(self, token: str) -> bool:
        """Revoking is org-scoped: a token is unguessable, but knowing one must
        not let another tenant burn it."""
        async with self.engine.begin() as conn:
            result = await conn.execute(
                update(share_links).where(
                    share_links.c.token == token,
                    share_links.c.org_id == current_org_id(),
                ).values(revoked=True)
            )
        return bool(result.rowcount)

    async def list_share_links(self) -> list[dict[str, Any]]:
        """Every link, across tenants — this feeds the boot rehydrate, which
        runs before any request and must repopulate the whole registry. Each
        row carries the org the link resolves under."""
        async with self.engine.connect() as conn:
            rows = (await conn.execute(select(share_links))).all()
        return [
            {"token": r.token, "run_id": r.run_id, "case_index": r.case_index,
             "revoked": r.revoked, "org_id": r.org_id}
            for r in rows
        ]

    # ── notification subscriptions (spec §16) ─────────────────────────────────

    async def add_subscription(
        self, url: str, triggers: list[str], debounce_seconds: float,
        label: str = "", node_pattern: str = "*",
    ) -> None:
        """Persist a subscription; idempotent on (url, trigger-set, pattern) so
        a repeated subscribe or a boot rehydrate never multiplies deliveries."""
        joined = ",".join(sorted(triggers))
        try:
            await self._add_subscription(url, joined, debounce_seconds, label,
                                         node_pattern)
        except IntegrityError:
            # The unique constraint decided; the row exists, so re-run and the
            # duplicate branch updates it instead.
            await self._add_subscription(url, joined, debounce_seconds, label,
                                         node_pattern)

    async def _add_subscription(
        self, url: str, joined: str, debounce_seconds: float,
        label: str, node_pattern: str,
    ) -> None:
        async with self.engine.begin() as conn:
            dup = (await conn.execute(
                select(notification_subs.c.id).where(
                    notification_subs.c.url == url,
                    notification_subs.c.triggers == joined,
                    notification_subs.c.node_pattern == node_pattern,
                    notification_subs.c.org_id == current_org_id(),
                )
            )).first()
            if dup is not None:
                await conn.execute(
                    update(notification_subs)
                    .where(notification_subs.c.id == dup.id)
                    .values(debounce_seconds=debounce_seconds, label=label)
                )
                return
            await conn.execute(insert(notification_subs).values(
                url=url, triggers=joined, debounce_seconds=debounce_seconds,
                label=label, node_pattern=node_pattern, org_id=current_org_id(),
            ))

    async def list_subscriptions(self) -> list[dict[str, Any]]:
        """This tenant's subscriptions — what the settings surface shows."""
        async with self.engine.connect() as conn:
            rows = (await conn.execute(
                select(notification_subs)
                .where(notification_subs.c.org_id == current_org_id())
            )).all()
        return [_subscription_row(r) for r in rows]

    async def all_subscriptions(self) -> list[dict[str, Any]]:
        """Every tenant's subscriptions, each carrying its org — the boot
        rehydrate, which runs before any request and must repopulate the whole
        in-memory notifier."""
        async with self.engine.connect() as conn:
            rows = (await conn.execute(select(notification_subs))).all()
        return [{**_subscription_row(r), "org_id": r.org_id} for r in rows]

    # ── settings KV (license, regression schedule) ────────────────────────────

    async def get_setting(self, key: str) -> Any | None:  # noqa: ANN401 - JSON value
        async with self.engine.connect() as conn:
            row = (await conn.execute(
                select(settings.c.value).where(
                    settings.c.key == key,
                    settings.c.org_id == current_org_id(),
                )
            )).first()
        return row.value if row is not None else None

    async def set_setting(self, key: str, value: Any) -> None:  # noqa: ANN401 - JSON
        """Write one KV row for this tenant. Retried once on a concurrent first
        write of the same key, which turns the insert into an update."""
        try:
            await self._set_setting(key, value)
        except IntegrityError:
            await self._set_setting(key, value)

    async def _set_setting(self, key: str, value: Any) -> None:  # noqa: ANN401
        async with self.engine.begin() as conn:
            existing = (await conn.execute(
                select(settings.c.key).where(
                    settings.c.key == key,
                    settings.c.org_id == current_org_id(),
                )
            )).first()
            if existing is None:
                await conn.execute(insert(settings).values(
                    key=key, value=value, org_id=current_org_id(),
                ))
            else:
                await conn.execute(
                    update(settings)
                    .where(
                        settings.c.key == key,
                        settings.c.org_id == current_org_id(),
                    )
                    .values(value=value)
                )

    async def orgs_with_setting(self, key: str) -> list[str]:
        """Every org that has stored `key`. A process-wide sweep (the EE
        regression scheduler) must run once per tenant that configured one,
        instead of only for whichever org happens to be ambient — which, in a
        background task, is always the public one."""
        async with self.engine.connect() as conn:
            rows = (await conn.execute(
                select(settings.c.org_id).where(settings.c.key == key)
            )).all()
        return sorted({r.org_id for r in rows})

    async def list_orgs(self) -> list[str]:
        """Every tenant with data this process sweeps over.

        The union of the tables the background loops touch: runs (retention),
        reported_state (the stale monitor), settings (the scheduler). Always
        includes the public tenant, so a single-tenant deployment sweeps exactly
        as it did before multi-tenancy existed."""
        async with self.engine.connect() as conn:
            found: set[str] = {PUBLIC_ORG}
            for column in (runs.c.org_id, reported_state.c.org_id,
                           settings.c.org_id):
                rows = (await conn.execute(select(column).distinct())).all()
                found |= {r[0] for r in rows}
        return sorted(found)

    # ── regression report history (EE surfaces it; every run records) ────────

    async def add_regression_report(
        self, report: dict[str, Any], source: str, created_ts: str,
    ) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(insert(regression_reports).values(
                created_ts=created_ts, source=source,
                regressed=report.get("regressed", 0),
                escaped=report.get("escaped", 0),
                skipped=len(report.get("skipped", [])),
                total=len(report.get("rows", [])),
                safe_to_ship=bool(report.get("safe_to_ship")),
                report_json=report, org_id=current_org_id(),
            ))

    async def list_regression_reports(self, limit: int = 50) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            rows = (await conn.execute(
                select(regression_reports)
                .where(regression_reports.c.org_id == current_org_id())
                .order_by(regression_reports.c.id.desc()).limit(limit)
            )).all()
        return [
            {"created_ts": r.created_ts, "source": r.source,
             "regressed": r.regressed, "escaped": r.escaped,
             "skipped": r.skipped, "total": r.total,
             "safe_to_ship": r.safe_to_ship}
            for r in rows
        ]

    # ── behavioral health checks (axor-probe batteries posted by a node) ─────

    async def add_probe_report(
        self, node_id: str, payload: dict[str, Any], ts: str,
    ) -> int:
        """Append one health check. Returns the new row id.

        Append-only by design: a re-probe after a heal is a NEW check, never an
        overwrite of the one that showed the drift. The heal→verify pair is only
        readable as a pair if both halves survive (ui-spec 8.2.1).
        """
        async with self.engine.begin() as conn:
            result = await conn.execute(insert(probe_reports).values(
                node_id=node_id, created_ts=ts,
                session_id=str(payload.get("session_id", "")),
                overall_verdict=str(payload.get("overall_verdict", "INCONCLUSIVE")),
                escape_count=int(payload.get("escape_count", 0)),
                probes_sent=int(payload.get("probes_sent", 0)),
                payload_json=payload, org_id=current_org_id(),
            ))
            return int(result.inserted_primary_key[0])

    async def latest_probe_report(self, node_id: str) -> dict[str, Any] | None:
        async with self.engine.connect() as conn:
            row = (await conn.execute(
                select(probe_reports)
                .where(
                    probe_reports.c.node_id == node_id,
                    probe_reports.c.org_id == current_org_id(),
                )
                .order_by(probe_reports.c.id.desc()).limit(1)
            )).first()
        if row is None:
            return None
        return {"id": row.id, "created_ts": row.created_ts, **row.payload_json}

    async def probe_report_history(
        self, node_id: str, limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Oldest-first summaries — the series the drift sparkline plots."""
        async with self.engine.connect() as conn:
            rows = (await conn.execute(
                select(probe_reports)
                .where(
                    probe_reports.c.node_id == node_id,
                    probe_reports.c.org_id == current_org_id(),
                )
                .order_by(probe_reports.c.id.desc()).limit(limit)
            )).all()
        return [
            {"id": r.id, "created_ts": r.created_ts, "session_id": r.session_id,
             "overall_verdict": r.overall_verdict, "escape_count": r.escape_count,
             "probes_sent": r.probes_sent}
            for r in reversed(rows)
        ]

    # ── Lab deploys (axor-cp-deploy/v1 packages accepted from Axor Lab) ──────

    async def add_lab_deploy(
        self, package_id: str, package: dict[str, Any], pins_created: int, ts: str,
    ) -> bool:
        """Store an accepted package; idempotent on package_id (a re-upload of
        the same bytes is acknowledged, never duplicated). Returns True when
        the row is new."""
        try:
            return await self._add_lab_deploy(package_id, package, pins_created, ts)
        except IntegrityError:
            return False  # a concurrent upload of the same bytes won the race

    async def _add_lab_deploy(
        self, package_id: str, package: dict[str, Any], pins_created: int, ts: str,
    ) -> bool:
        async with self.engine.begin() as conn:
            dup = (await conn.execute(
                select(lab_deploys.c.package_id)
                .where(
                    lab_deploys.c.package_id == package_id,
                    lab_deploys.c.org_id == current_org_id(),
                )
            )).first()
            if dup is not None:
                return False
            await conn.execute(insert(lab_deploys).values(
                package_id=package_id, created_ts=ts,
                kernel=str(package.get("kernel", "")),
                config_hash=str(package.get("config_hash", "")),
                parametric_config_hash=str(package.get("parametric_config_hash", "")),
                pins_created=pins_created,
                manifest_count=len(package.get("tool_manifests", [])),
                package_json=package, org_id=current_org_id(),
            ))
            return True

    async def list_lab_deploys(self) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            rows = (await conn.execute(
                select(lab_deploys)
                .where(lab_deploys.c.org_id == current_org_id())
                .order_by(lab_deploys.c.created_ts.desc())
            )).all()
        return [
            {"package_id": r.package_id, "created_ts": r.created_ts,
             "kernel": r.kernel, "config_hash": r.config_hash,
             "parametric_config_hash": r.parametric_config_hash,
             "pins_created": r.pins_created, "manifest_count": r.manifest_count,
             "source": (r.package_json or {}).get("source", {})}
            for r in rows
        ]

    async def get_lab_deploy(self, package_id: str) -> dict[str, Any] | None:
        async with self.engine.connect() as conn:
            row = (await conn.execute(
                select(lab_deploys.c.package_json)
                .where(
                    lab_deploys.c.package_id == package_id,
                    lab_deploys.c.org_id == current_org_id(),
                )
            )).first()
        return row.package_json if row is not None else None

    # ── notification dead letters (persist: a restart must not erase the
    # evidence that deliveries were lost) ─────────────────────────────────────

    async def add_dead_letter(
        self, url: str, payload: dict[str, Any], error: str, attempts: int,
        created_ts: str, cap: int = 500, org: str | None = None,
    ) -> None:
        """`org` is passed explicitly: a dead letter is written after the retry
        backoff, potentially far from the request that emitted it, so the
        tenant is captured at emit time rather than read from the ambient
        context here."""
        tenant = org if org is not None else current_org_id()
        async with self.engine.begin() as conn:
            await conn.execute(insert(dead_letters).values(
                url=url, payload_json=payload, error=error[:500],
                attempts=attempts, created_ts=created_ts, org_id=tenant,
            ))
            # Keep only the newest `cap` rows OF THIS TENANT — the same bound the
            # in-memory deque had, enforced in SQL so the table cannot grow
            # without limit. The cap used to be global, which made this the one
            # table where a tenant could destroy another tenant's data: an org
            # with a broken webhook evicted everyone else's dead letters, and a
            # dead letter is precisely the evidence that deliveries were lost.
            keep = (
                select(dead_letters.c.id)
                .where(dead_letters.c.org_id == tenant)
                .order_by(dead_letters.c.id.desc())
                .limit(cap)
                .subquery()
            )
            await conn.execute(
                delete(dead_letters).where(
                    dead_letters.c.org_id == tenant,
                    dead_letters.c.id.not_in(select(keep.c.id)),
                )
            )

    async def list_dead_letters(self, limit: int = 500) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            rows = (await conn.execute(
                select(dead_letters)
                .where(dead_letters.c.org_id == current_org_id())
                .order_by(dead_letters.c.id.desc()).limit(limit)
            )).all()
        return [
            {"url": r.url, "payload": r.payload_json, "error": r.error,
             "attempts": r.attempts, "created_ts": r.created_ts}
            for r in rows
        ]


async def _stored_coordinates(
    conn: Any,  # noqa: ANN401 - SQLAlchemy AsyncConnection
    run_id: str,
    org: str,
    lines: list[dict[str, Any]],
    default_node: str,
) -> set[tuple[str, int]]:
    """The ``(node_id, seq)`` pairs of this batch that the run ALREADY holds.

    Bounded by the batch, not by the run. Loading every coordinate of the run
    made each append cost more as the run grew — quadratic over a long-lived
    node's keepalive run. The node/seq filters are a superset of the batch (a
    cross product, not tuple-IN, which is not portable), so the result is
    intersected with the batch before it is returned.
    """
    wanted = {(str(ln.get("node_id", default_node)), int(ln["seq"])) for ln in lines}
    if not wanted:
        return set()
    rows = (await conn.execute(
        select(events.c.node_id, events.c.seq).where(
            events.c.run_id == run_id,
            events.c.org_id == org,
            events.c.node_id.in_({n for n, _ in wanted}),
            events.c.seq.in_({s for _, s in wanted}),
        )
    )).all()
    return {(r.node_id, r.seq) for r in rows} & wanted


def _run_row(row: Any) -> dict[str, Any]:  # noqa: ANN401 - SQLAlchemy Row
    return {
        "run_id": row.run_id, "node_id": row.node_id, "scenario": row.scenario,
        "intervened": row.intervened, "completed": row.completed,
        "evidence": row.evidence_json, "created_ts": row.created_ts,
    }


def _subscription_row(row: Any) -> dict[str, Any]:  # noqa: ANN401 - SQLAlchemy Row
    return {
        "url": row.url,
        "triggers": [t for t in row.triggers.split(",") if t],
        "debounce_seconds": row.debounce_seconds,
        "label": row.label,
        "node_pattern": row.node_pattern,
    }
