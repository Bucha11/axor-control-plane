"""System of record: append-only events + runs/desired/reported/facts/pins.

Postgres in production (asyncpg), SQLite (aiosqlite) for dev and tests — the
schema sticks to portable types. Events are append-only; desired state is
versioned LWW (the lattice semantics live in axor_core.kernel.state — the
backend persists and fans out, it does not interpret).
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    delete,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

metadata = MetaData()

# JSON payload columns: real JSONB on Postgres (indexable, queryable), portable
# JSON (stored as TEXT, auto-(de)serialised) on SQLite for dev/tests. One column
# type, both deploys — the backend stops treating governance payloads as opaque
# blobs the moment it runs on Postgres.
_JSON = JSON().with_variant(JSONB(), "postgresql")

runs = Table(
    "runs", metadata,
    Column("run_id", String(64), primary_key=True),
    Column("node_id", String(128), nullable=False),
    Column("scenario", String(128), nullable=False, default="custom"),
    Column("intervened", Boolean, nullable=False, default=False),
    Column("completed", Boolean, nullable=False, default=False),
    Column("evidence_json", _JSON, nullable=False, default=list),
    Column("created_ts", String(40), nullable=False),
)

events = Table(
    "events", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String(64), nullable=False, index=True),
    Column("node_id", String(128), nullable=False, index=True),
    Column("seq", Integer, nullable=False),
    Column("kind", String(40), nullable=False),
    Column("line", _JSON, nullable=False),  # full kernel-schema JSON line
    # Per-node sequences (spec v2 Ch.4 §5): seq is monotonic PER NODE, so a
    # multi-node run legitimately repeats seq across nodes.
    UniqueConstraint("run_id", "node_id", "seq", name="uq_events_run_node_seq"),
)

ingest_keys = Table(
    "ingest_keys", metadata,
    Column("key", String(128), primary_key=True),
)

desired_state = Table(
    "desired_state", metadata,
    Column("node_id", String(128), primary_key=True),
    Column("version", Integer, nullable=False),
    Column("state_json", _JSON, nullable=False),
)

reported_state = Table(
    "reported_state", metadata,
    Column("node_id", String(128), primary_key=True),
    Column("applied_version", Integer, nullable=False, default=0),
    Column("level", String(24), nullable=False, default="NORMAL"),
    Column("budget_remaining", Integer, nullable=True),
    Column("updated_ts", String(40), nullable=False),
)

facts = Table(
    "facts", metadata,
    Column("fact_id", String(128), primary_key=True),
    Column("node_id", String(128), nullable=False, index=True),
    Column("fact_json", _JSON, nullable=False),
    Column("created_ts", String(40), nullable=False),
)

pins = Table(
    "pins", metadata,
    Column("run_id", String(64), primary_key=True),
    Column("side", String(16), nullable=False),  # must_block | must_pass
    Column("label", String(200), nullable=False, default=""),
)

# Share links & notification subscriptions are PRIMARY user data (a revocable
# permalink; a webhook the on-call registered), not a derived index like the
# taint graph — so they persist here and rehydrate into their in-memory holders
# at boot. Without this a restart 404s every shared EvidenceCase and silently
# stops every notification.
share_links = Table(
    "share_links", metadata,
    Column("token", String(64), primary_key=True),
    Column("run_id", String(64), nullable=False),
    Column("case_index", Integer, nullable=False),
    Column("revoked", Boolean, nullable=False, default=False),
    Column("created_ts", String(40), nullable=False),
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
    UniqueConstraint("url", "triggers", "node_pattern", name="uq_sub_url_triggers"),
)

# Small KV for operator-set runtime state that must survive restarts: the
# active EE license, the regression schedule. JSON values, single row per key.
settings = Table(
    "settings", metadata,
    Column("key", String(64), primary_key=True),
    Column("value", _JSON, nullable=False),
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
)

# Accepted Lab deploy packages (axor-cp-deploy/v1, migration 0005): the record
# of every finalized Lab handoff — validated policy + manifests stored verbatim
# (package_json), plus the summary columns the list surface reads. The pins the
# package created live in `pins` (labelled lab:{package_id}); this table is the
# provenance of where they came from.
lab_deploys = Table(
    "lab_deploys", metadata,
    Column("package_id", String(64), primary_key=True),
    Column("created_ts", String(40), nullable=False),
    Column("kernel", String(120), nullable=False),
    Column("config_hash", String(80), nullable=False),
    Column("parametric_config_hash", String(80), nullable=False),
    Column("pins_created", Integer, nullable=False, default=0),
    Column("manifest_count", Integer, nullable=False, default=0),
    Column("package_json", _JSON, nullable=False),
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
)

api_keys = Table(
    "api_keys", metadata,
    Column("key_id", String(32), primary_key=True),
    Column("hashed_secret", String(64), nullable=False),  # sha256 hex
    Column("scopes", String(200), nullable=False),        # comma-separated
    Column("label", String(200), nullable=False, default=""),
    Column("created_ts", String(40), nullable=False),
)


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
        async with self.engine.begin() as conn:
            existing = (
                await conn.execute(select(runs.c.run_id).where(runs.c.run_id == run_id))
            ).first()
            if existing is None:
                await conn.execute(insert(runs).values(
                    run_id=run_id, node_id=node_id, scenario=scenario,
                    intervened=False, completed=False, evidence_json=[],
                    created_ts=ts,
                ))

    async def ingest_events(
        self, run_id: str, node_id: str, lines: list[dict[str, Any]],
        idempotency_key: str | None,
    ) -> int:
        """Append a telemetry batch. Returns number of stored events (0 on
        duplicate batch or duplicate seqs — dedupe, never double-append)."""
        async with self.engine.begin() as conn:
            if idempotency_key:
                dup = (await conn.execute(
                    select(ingest_keys.c.key).where(ingest_keys.c.key == idempotency_key)
                )).first()
                if dup is not None:
                    return 0
                await conn.execute(insert(ingest_keys).values(key=idempotency_key))
            seen = {
                (row.node_id, row.seq) for row in (await conn.execute(
                    select(events.c.node_id, events.c.seq)
                    .where(events.c.run_id == run_id)
                )).all()
            }
            stored = 0
            for line in lines:
                if (line.get("node_id", node_id), line["seq"]) in seen:
                    continue
                await conn.execute(insert(events).values(
                    run_id=run_id,
                    # Multi-node runs carry per-line node identity (spec v2
                    # Ch.4); fall back to the batch's node for legacy lines.
                    node_id=line.get("node_id", node_id),
                    seq=line["seq"],
                    kind=line["kind"],
                    line=line,
                ))
                stored += 1
            return stored

    async def run_events(self, run_id: str, after_seq: int = -1) -> list[str]:
        async with self.engine.connect() as conn:
            rows = (await conn.execute(
                select(events.c.line)
                .where(events.c.run_id == run_id, events.c.seq > after_seq)
                .order_by(events.c.seq)
            )).all()
        # The column is native JSON; callers of this method still expect the raw
        # kernel-schema line as a string, so re-serialise on the way out.
        return [json.dumps(r.line) for r in rows]

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
                    .where(events.c.run_id == run_id)
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
                ))
                seen.add((node_id, seq))
                stored += 1
            return stored

    async def list_runs(self) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            rows = (await conn.execute(
                select(runs).order_by(runs.c.created_ts.desc())
            )).all()
        return [
            {
                "run_id": r.run_id, "node_id": r.node_id, "scenario": r.scenario,
                "intervened": r.intervened, "completed": r.completed,
                "evidence": r.evidence_json, "created_ts": r.created_ts,
            }
            for r in rows
        ]

    async def set_evidence(self, run_id: str, evidence: list[dict[str, Any]]) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                update(runs).where(runs.c.run_id == run_id).values(
                    evidence_json=evidence, completed=True,
                )
            )

    async def mark_intervened(self, run_id: str) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                update(runs).where(runs.c.run_id == run_id).values(intervened=True)
            )

    # ── desired / reported state ──────────────────────────────────────────────

    async def bump_desired(self, node_id: str, delta: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Apply a declarative delta, assign the next version, persist, return
        (version, merged_state). LWW per key; `stopped` absorbing is enforced
        at the adapter — the backend stores what was commanded."""
        async with self.engine.begin() as conn:
            row = (await conn.execute(
                select(desired_state).where(desired_state.c.node_id == node_id)
            )).first()
            if row is None:
                version, state = 1, dict(delta)
                await conn.execute(insert(desired_state).values(
                    node_id=node_id, version=version, state_json=state,
                ))
            else:
                version = row.version + 1
                state = {**row.state_json, **delta}
                await conn.execute(
                    update(desired_state)
                    .where(desired_state.c.node_id == node_id)
                    .values(version=version, state_json=state)
                )
            return version, state

    async def get_desired(self, node_id: str) -> tuple[int, dict[str, Any]] | None:
        async with self.engine.connect() as conn:
            row = (await conn.execute(
                select(desired_state).where(desired_state.c.node_id == node_id)
            )).first()
        if row is None:
            return None
        return row.version, row.state_json

    async def clear_desired_key(self, node_id: str, key: str) -> None:
        """Consumption ack (injection/excision): clear the one-shot from state."""
        async with self.engine.begin() as conn:
            row = (await conn.execute(
                select(desired_state).where(desired_state.c.node_id == node_id)
            )).first()
            if row is None:
                return
            state = dict(row.state_json)
            if key in state:
                state.pop(key)
                await conn.execute(
                    update(desired_state)
                    .where(desired_state.c.node_id == node_id)
                    .values(version=row.version + 1, state_json=state)
                )

    async def upsert_reported(
        self, node_id: str, applied_version: int, level: str,
        budget_remaining: int | None, ts: str,
    ) -> None:
        async with self.engine.begin() as conn:
            row = (await conn.execute(
                select(reported_state.c.node_id)
                .where(reported_state.c.node_id == node_id)
            )).first()
            values = {
                "applied_version": applied_version, "level": level,
                "budget_remaining": budget_remaining, "updated_ts": ts,
            }
            if row is None:
                await conn.execute(insert(reported_state).values(
                    node_id=node_id, **values,
                ))
            else:
                await conn.execute(
                    update(reported_state)
                    .where(reported_state.c.node_id == node_id).values(**values)
                )

    async def get_reported(self, node_id: str) -> dict[str, Any] | None:
        async with self.engine.connect() as conn:
            row = (await conn.execute(
                select(reported_state).where(reported_state.c.node_id == node_id)
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
                )
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
                .where(events.c.kind.in_(
                    ("node_spawned", "message_sent", "message_received")
                ))
                .order_by(events.c.run_id, events.c.node_id, events.c.seq)
            )).all()
        return [r.line for r in rows]

    async def list_nodes(self) -> list[str]:
        async with self.engine.connect() as conn:
            desired = (await conn.execute(select(desired_state.c.node_id))).all()
            reported = (await conn.execute(select(reported_state.c.node_id))).all()
        return sorted({r.node_id for r in desired} | {r.node_id for r in reported})

    # ── facts ─────────────────────────────────────────────────────────────────

    async def append_fact(self, node_id: str, fact: dict[str, Any], ts: str) -> bool:
        """Append-only: a duplicate fact_id is refused, never replaced."""
        async with self.engine.begin() as conn:
            dup = (await conn.execute(
                select(facts.c.fact_id).where(facts.c.fact_id == fact["fact_id"])
            )).first()
            if dup is not None:
                return False
            await conn.execute(insert(facts).values(
                fact_id=fact["fact_id"], node_id=node_id,
                fact_json=fact, created_ts=ts,
            ))
            return True

    async def node_facts(self, node_id: str) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            rows = (await conn.execute(
                select(facts.c.fact_json)
                .where(facts.c.node_id == node_id)
                .order_by(facts.c.created_ts)
            )).all()
        return [r.fact_json for r in rows]

    async def all_facts(self) -> list[dict[str, Any]]:
        """Every fact across all nodes, oldest first — the graph rehydrator folds
        attestations from here (a fact can exist for a node with no plane state)."""
        async with self.engine.connect() as conn:
            rows = (await conn.execute(
                select(facts.c.fact_json).order_by(facts.c.created_ts)
            )).all()
        return [r.fact_json for r in rows]

    # ── API keys (auth, architecture section 9) ───────────────────────────────

    async def create_api_key(
        self, key_id: str, hashed_secret: str, scopes: list[str],
        label: str, ts: str,
    ) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(insert(api_keys).values(
                key_id=key_id, hashed_secret=hashed_secret,
                scopes=",".join(scopes), label=label, created_ts=ts,
            ))

    async def get_api_key(self, key_id: str) -> dict[str, Any] | None:
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
        }

    async def list_api_keys(self) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            rows = (await conn.execute(select(api_keys))).all()
        return [
            {"key_id": r.key_id, "scopes": [s for s in r.scopes.split(",") if s],
             "label": r.label, "created_ts": r.created_ts}
            for r in rows
        ]

    async def delete_api_key(self, key_id: str) -> bool:
        from sqlalchemy import delete
        async with self.engine.begin() as conn:
            result = await conn.execute(
                delete(api_keys).where(api_keys.c.key_id == key_id)
            )
        return bool(result.rowcount)

    # ── pins (regression corpus, decision 11) ─────────────────────────────────

    async def pin(self, run_id: str, side: str, label: str = "") -> None:
        async with self.engine.begin() as conn:
            dup = (await conn.execute(
                select(pins.c.run_id).where(pins.c.run_id == run_id)
            )).first()
            if dup is None:
                await conn.execute(insert(pins).values(
                    run_id=run_id, side=side, label=label,
                ))
            else:
                await conn.execute(
                    update(pins).where(pins.c.run_id == run_id)
                    .values(side=side, label=label)
                )

    async def prune_runs_older_than(self, cutoff_ts: str) -> int:
        """Retention (launch-readiness §1): delete runs created before the ISO
        cutoff, with their events, pins and share links. Plane state (facts,
        desired/reported) is node-scoped operational state and is kept.
        created_ts is ISO-8601, so lexicographic compare is chronological."""
        from sqlalchemy import delete

        async with self.engine.begin() as conn:
            old_ids = [
                r.run_id for r in (await conn.execute(
                    select(runs.c.run_id).where(runs.c.created_ts < cutoff_ts)
                )).all()
            ]
            if not old_ids:
                return 0
            await conn.execute(delete(events).where(events.c.run_id.in_(old_ids)))
            await conn.execute(delete(pins).where(pins.c.run_id.in_(old_ids)))
            await conn.execute(
                delete(share_links).where(share_links.c.run_id.in_(old_ids))
            )
            await conn.execute(delete(runs).where(runs.c.run_id.in_(old_ids)))
            return len(old_ids)

    async def pinned(self) -> list[dict[str, str]]:
        async with self.engine.connect() as conn:
            rows = (await conn.execute(select(pins))).all()
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
                revoked=False, created_ts=ts,
            ))

    async def revoke_share_link(self, token: str) -> bool:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                update(share_links).where(share_links.c.token == token)
                .values(revoked=True)
            )
        return bool(result.rowcount)

    async def list_share_links(self) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            rows = (await conn.execute(select(share_links))).all()
        return [
            {"token": r.token, "run_id": r.run_id, "case_index": r.case_index,
             "revoked": r.revoked}
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
        async with self.engine.begin() as conn:
            dup = (await conn.execute(
                select(notification_subs.c.id).where(
                    notification_subs.c.url == url,
                    notification_subs.c.triggers == joined,
                    notification_subs.c.node_pattern == node_pattern,
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
                label=label, node_pattern=node_pattern,
            ))

    async def list_subscriptions(self) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            rows = (await conn.execute(select(notification_subs))).all()
        return [
            {"url": r.url,
             "triggers": [t for t in r.triggers.split(",") if t],
             "debounce_seconds": r.debounce_seconds,
             "label": r.label, "node_pattern": r.node_pattern}
            for r in rows
        ]

    # ── settings KV (license, regression schedule) ────────────────────────────

    async def get_setting(self, key: str) -> Any | None:  # noqa: ANN401 - JSON value
        async with self.engine.connect() as conn:
            row = (await conn.execute(
                select(settings.c.value).where(settings.c.key == key)
            )).first()
        return row.value if row is not None else None

    async def set_setting(self, key: str, value: Any) -> None:  # noqa: ANN401 - JSON
        async with self.engine.begin() as conn:
            existing = (await conn.execute(
                select(settings.c.key).where(settings.c.key == key)
            )).first()
            if existing is None:
                await conn.execute(insert(settings).values(key=key, value=value))
            else:
                await conn.execute(
                    update(settings).where(settings.c.key == key).values(value=value)
                )

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
                report_json=report,
            ))

    async def list_regression_reports(self, limit: int = 50) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            rows = (await conn.execute(
                select(regression_reports)
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
                payload_json=payload,
            ))
            return int(result.inserted_primary_key[0])

    async def latest_probe_report(self, node_id: str) -> dict[str, Any] | None:
        async with self.engine.connect() as conn:
            row = (await conn.execute(
                select(probe_reports)
                .where(probe_reports.c.node_id == node_id)
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
                .where(probe_reports.c.node_id == node_id)
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
        async with self.engine.begin() as conn:
            dup = (await conn.execute(
                select(lab_deploys.c.package_id)
                .where(lab_deploys.c.package_id == package_id)
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
                package_json=package,
            ))
            return True

    async def list_lab_deploys(self) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            rows = (await conn.execute(
                select(lab_deploys).order_by(lab_deploys.c.created_ts.desc())
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
                .where(lab_deploys.c.package_id == package_id)
            )).first()
        return row.package_json if row is not None else None

    # ── notification dead letters (persist: a restart must not erase the
    # evidence that deliveries were lost) ─────────────────────────────────────

    async def add_dead_letter(
        self, url: str, payload: dict[str, Any], error: str, attempts: int,
        created_ts: str, cap: int = 500,
    ) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(insert(dead_letters).values(
                url=url, payload_json=payload, error=error[:500],
                attempts=attempts, created_ts=created_ts,
            ))
            # Keep only the newest `cap` rows — same bound the in-memory deque
            # had, enforced in SQL so the table cannot grow without limit.
            keep = select(dead_letters.c.id).order_by(
                dead_letters.c.id.desc()
            ).limit(cap).subquery()
            await conn.execute(
                delete(dead_letters).where(
                    dead_letters.c.id.not_in(select(keep.c.id))
                )
            )

    async def list_dead_letters(self, limit: int = 500) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            rows = (await conn.execute(
                select(dead_letters).order_by(dead_letters.c.id.desc()).limit(limit)
            )).all()
        return [
            {"url": r.url, "payload": r.payload_json, "error": r.error,
             "attempts": r.attempts, "created_ts": r.created_ts}
            for r in rows
        ]
