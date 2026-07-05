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
    Boolean,
    Column,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    insert,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

metadata = MetaData()

runs = Table(
    "runs", metadata,
    Column("run_id", String(64), primary_key=True),
    Column("node_id", String(128), nullable=False),
    Column("scenario", String(128), nullable=False, default="custom"),
    Column("intervened", Boolean, nullable=False, default=False),
    Column("completed", Boolean, nullable=False, default=False),
    Column("evidence_json", Text, nullable=False, default="[]"),
    Column("created_ts", String(40), nullable=False),
)

events = Table(
    "events", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String(64), nullable=False, index=True),
    Column("node_id", String(128), nullable=False, index=True),
    Column("seq", Integer, nullable=False),
    Column("kind", String(40), nullable=False),
    Column("line", Text, nullable=False),  # full kernel-schema JSON line
    UniqueConstraint("run_id", "seq", name="uq_events_run_seq"),
)

ingest_keys = Table(
    "ingest_keys", metadata,
    Column("key", String(128), primary_key=True),
)

desired_state = Table(
    "desired_state", metadata,
    Column("node_id", String(128), primary_key=True),
    Column("version", Integer, nullable=False),
    Column("state_json", Text, nullable=False),
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
    Column("fact_json", Text, nullable=False),
    Column("created_ts", String(40), nullable=False),
)

pins = Table(
    "pins", metadata,
    Column("run_id", String(64), primary_key=True),
    Column("side", String(16), nullable=False),  # must_block | must_pass
    Column("label", String(200), nullable=False, default=""),
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


async def init_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)


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
                    intervened=False, completed=False, evidence_json="[]",
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
                row.seq for row in (await conn.execute(
                    select(events.c.seq).where(events.c.run_id == run_id)
                )).all()
            }
            stored = 0
            for line in lines:
                if line["seq"] in seen:
                    continue
                await conn.execute(insert(events).values(
                    run_id=run_id,
                    node_id=node_id,
                    seq=line["seq"],
                    kind=line["kind"],
                    line=json.dumps(line, sort_keys=True),
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
        return [r.line for r in rows]

    async def list_runs(self) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            rows = (await conn.execute(
                select(runs).order_by(runs.c.created_ts.desc())
            )).all()
        return [
            {
                "run_id": r.run_id, "node_id": r.node_id, "scenario": r.scenario,
                "intervened": r.intervened, "completed": r.completed,
                "evidence": json.loads(r.evidence_json), "created_ts": r.created_ts,
            }
            for r in rows
        ]

    async def set_evidence(self, run_id: str, evidence: list[dict[str, Any]]) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                update(runs).where(runs.c.run_id == run_id).values(
                    evidence_json=json.dumps(evidence), completed=True,
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
                    node_id=node_id, version=version, state_json=json.dumps(state),
                ))
            else:
                version = row.version + 1
                state = {**json.loads(row.state_json), **delta}
                await conn.execute(
                    update(desired_state)
                    .where(desired_state.c.node_id == node_id)
                    .values(version=version, state_json=json.dumps(state))
                )
            return version, state

    async def get_desired(self, node_id: str) -> tuple[int, dict[str, Any]] | None:
        async with self.engine.connect() as conn:
            row = (await conn.execute(
                select(desired_state).where(desired_state.c.node_id == node_id)
            )).first()
        if row is None:
            return None
        return row.version, json.loads(row.state_json)

    async def clear_desired_key(self, node_id: str, key: str) -> None:
        """Consumption ack (injection/excision): clear the one-shot from state."""
        async with self.engine.begin() as conn:
            row = (await conn.execute(
                select(desired_state).where(desired_state.c.node_id == node_id)
            )).first()
            if row is None:
                return
            state = json.loads(row.state_json)
            if key in state:
                state.pop(key)
                await conn.execute(
                    update(desired_state)
                    .where(desired_state.c.node_id == node_id)
                    .values(version=row.version + 1, state_json=json.dumps(state))
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
                fact_json=json.dumps(fact, sort_keys=True), created_ts=ts,
            ))
            return True

    async def node_facts(self, node_id: str) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            rows = (await conn.execute(
                select(facts.c.fact_json)
                .where(facts.c.node_id == node_id)
                .order_by(facts.c.created_ts)
            )).all()
        return [json.loads(r.fact_json) for r in rows]

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

    async def pinned(self) -> list[dict[str, str]]:
        async with self.engine.connect() as conn:
            rows = (await conn.execute(select(pins))).all()
        return [
            {"run_id": r.run_id, "side": r.side, "label": r.label} for r in rows
        ]
