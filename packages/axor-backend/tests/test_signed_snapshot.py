"""The desired-state snapshot carries the signed command behind each key.

Protocol v0.3 §3. The backend half of the fix: §6 says a compromised plane
cannot forge a pause, stop, injection or attestation, and that was only true of
deltas — the snapshot the stream opens with carried bare state, so a plane could
forge anything by sending it that way instead. The adapter now verifies every
key of a snapshot, which it can only do if the plane keeps the command that
wrote each one. `desired_state.commands_json` is that record (migration 0014).

What must NOT get recorded is as load-bearing as what must: an unsigned write
DROPS the affected keys' commands rather than leaving the previous signature in
place, or an internal bump would inherit a signature given for another value and
the adapter would apply the new one as if an operator had signed it.
"""
from __future__ import annotations

import json
import pathlib

import httpx
import pytest
from axor_backend.app import create_app
from axor_core.kernel.jcs import canonicalize

cryptography = pytest.importorskip("cryptography")
from cryptography.hazmat.primitives.asymmetric import ed25519  # noqa: E402

KEY = ed25519.Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
PUB = KEY.public_key().public_bytes_raw().hex()
TS = "2026-09-11T00:00:00Z"


def _sig(node: str, version: int, body: dict) -> str:
    return KEY.sign(canonicalize(
        {"node_id": node, "version": version, "body": body, "timestamp": TS}
    )).hex()


@pytest.fixture
async def client(tmp_path: pathlib.Path) -> httpx.AsyncClient:
    app = create_app(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/axor.db",
        operator_keys={"op_real": PUB}, allow_unsigned=False,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://backend.test"
    ) as c, app.router.lifespan_context(app):
        c._app = app  # type: ignore[attr-defined]
        yield c


async def _command(c: httpx.AsyncClient, node: str, version: int,
                   delta: dict) -> httpx.Response:
    return await c.post(f"/v1/plane/{node}/command", json={
        "state": delta, "version": version, "operator": "op_real",
        "timestamp": TS, "sig": _sig(node, version, delta),
    })


async def _snapshot(c: httpx.AsyncClient, node: str) -> dict:
    """What `plane.desired_stream` puts in its `snapshot` event. Built from the
    store because httpx's ASGI transport will not interleave an endless SSE
    body; the route composes exactly these two calls."""
    store = c._app.state.store  # type: ignore[attr-defined]
    version, state = await store.get_desired(node)
    return {"node_id": node, "version": version, "state": state,
            "commands": await store.desired_commands(node)}


class TestTheSnapshotCarriesItsSignatures:
    async def test_one_command_one_entry(self, client: httpx.AsyncClient) -> None:
        assert (await _command(client, "n1", 1, {"paused": True})).status_code == 202
        snap = await _snapshot(client, "n1")
        assert snap["state"] == {"paused": True}
        assert len(snap["commands"]) == 1
        entry = snap["commands"][0]
        assert entry["version"] == 1
        assert entry["delta"] == {"paused": True}
        assert entry["operator"] == "op_real"
        assert entry["sig"] == _sig("n1", 1, {"paused": True})

    async def test_every_key_of_the_state_is_accounted_for(
        self, client: httpx.AsyncClient,
    ) -> None:
        await _command(client, "n1", 1, {"paused": True})
        await _command(client, "n1", 2, {"budget_cap_calls": 50})
        snap = await _snapshot(client, "n1")
        covered: set[str] = set()
        for entry in snap["commands"]:
            covered |= set(entry["delta"])
        assert set(snap["state"]) <= covered

    async def test_a_rewritten_key_keeps_only_the_last_command(
        self, client: httpx.AsyncClient,
    ) -> None:
        """Kept per key, not as a log: bounded by the lattice, not by uptime."""
        for version in range(1, 6):
            await _command(client, "n1", version, {"paused": version % 2 == 1})
        snap = await _snapshot(client, "n1")
        assert len(snap["commands"]) == 1
        assert snap["commands"][0]["version"] == 5
        assert snap["commands"][0]["delta"] == snap["state"]

    async def test_the_entries_are_oldest_first(
        self, client: httpx.AsyncClient,
    ) -> None:
        """The adapter replays them in version order, so the lattice resolves
        the same way it would have live."""
        await _command(client, "n1", 1, {"stopped": True})
        await _command(client, "n1", 2, {"paused": True})
        snap = await _snapshot(client, "n1")
        versions = [e["version"] for e in snap["commands"]]
        assert versions == sorted(versions)

    async def test_one_command_writing_two_keys_is_sent_once(
        self, client: httpx.AsyncClient,
    ) -> None:
        await _command(client, "n1", 1, {"paused": True, "budget_cap_calls": 9})
        snap = await _snapshot(client, "n1")
        assert len(snap["commands"]) == 1


class TestAnUnsignedWriteDoesNotBorrowASignature:
    async def test_a_consumption_ack_drops_the_one_shots_command(
        self, client: httpx.AsyncClient,
    ) -> None:
        """The ack clears the key, so its command has nothing to vouch for."""
        injection = {"id": "i1", "text": "hello"}
        await _command(client, "n1", 1, {"pending_injection": injection})
        assert len((await _snapshot(client, "n1"))["commands"]) == 1
        r = await client.post("/v1/plane/n1/consumed",
                              json={"key": "pending_injection"})
        assert r.status_code == 200
        snap = await _snapshot(client, "n1")
        assert "pending_injection" not in snap["state"]
        assert snap["commands"] == []

    async def test_an_open_deployment_records_nothing(
        self, tmp_path: pathlib.Path,
    ) -> None:
        """No keyring means no signature was checked, and recording the
        caller's unverified claim would hand the adapter a `sig` that fails
        rather than the truth, which is that nothing here was signed."""
        app = create_app(
            database_url=f"sqlite+aiosqlite:///{tmp_path}/open.db",
            operator_keys={}, allow_unsigned=True,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t",
        ) as c, app.router.lifespan_context(app):
            r = await c.post("/v1/plane/n1/command", json={
                "state": {"paused": True}, "version": 1,
                "operator": "whoever", "timestamp": TS, "sig": "made-up",
            })
            assert r.status_code == 202
            assert await app.state.store.desired_commands("n1") == []


class TestTheStreamEmitsThem:
    def test_the_snapshot_event_names_commands(self) -> None:
        """Guards the route rather than the store: the field has to be on the
        wire, under that name, or the adapter has nothing to verify against."""
        source = (
            pathlib.Path(__file__).resolve().parents[1]
            / "src" / "axor_backend" / "plane.py"
        ).read_text("utf-8")
        emit = source[source.index('"event": "snapshot"'):][:400]
        assert '"commands"' in emit
        assert "desired_commands" in emit


def _adapter_verifies_snapshots() -> bool:
    """Does the INSTALLED axor-wrap take a snapshot's commands?

    The backend half of protocol v0.3 is useless on its own: the plane can send
    `commands` all it likes, and a released adapter that still applies bare
    state is the hole, unchanged. Until axor-wrap ships the other half, this
    skips rather than passing — a green round-trip against an adapter that
    ignores the field would be the most misleading test in the suite.
    """
    import inspect
    try:
        from axor_wrap.plane.session import PlaneSession
    except ImportError:
        return False
    return "commands" in inspect.signature(PlaneSession.apply_snapshot).parameters


@pytest.mark.skipif(
    not _adapter_verifies_snapshots(),
    reason="installed axor-wrap predates protocol v0.3 (snapshot commands)",
)
class TestTheRoundTrip:
    async def test_the_adapter_accepts_what_this_plane_sends(
        self, client: httpx.AsyncClient,
    ) -> None:
        """The two halves meet. Signed here, verified there, with no shared
        code between them beyond the kernel's canonicalizer."""
        session = pytest.importorskip("axor_wrap.plane.session")
        await _command(client, "n1", 1, {"paused": True})
        snap = await _snapshot(client, "n1")
        node = session.PlaneSession(node_id="n1",
                                    operator_pubkeys={"op_real": PUB})
        effect = node.apply_snapshot(
            snap["version"], snap["state"], snap["commands"],
        )
        assert effect.kind == "applied"
        assert node.paused is True

    async def test_and_refuses_the_same_state_with_the_commands_stripped(
        self, client: httpx.AsyncClient,
    ) -> None:
        session = pytest.importorskip("axor_wrap.plane.session")
        await _command(client, "n1", 1, {"paused": True})
        snap = await _snapshot(client, "n1")
        node = session.PlaneSession(node_id="n1",
                                    operator_pubkeys={"op_real": PUB})
        effect = node.apply_snapshot(snap["version"], snap["state"], [])
        assert effect.kind == "sig_invalid"
        assert node.paused is False


class TestTheWireShapeIsJsonSafe:
    async def test_the_snapshot_serialises(
        self, client: httpx.AsyncClient,
    ) -> None:
        await _command(client, "n1", 1, {"paused": True})
        json.dumps(await _snapshot(client, "n1"), sort_keys=True)


class TestRecordingIsPerKeyAndPerSignature:
    """Unit-level, because the one route that writes desired state always
    carries a signature when the keyring is loaded. The unsigned branch is
    reached by a deployment that turned signing OFF while signed records
    survive from before."""

    @staticmethod
    def _record(recorded: dict, delta: dict, version: int,
                signature: dict | None) -> dict:
        from axor_backend.storage import _record_commands

        return _record_commands(recorded, delta, version, signature)

    def test_a_signed_delta_records_every_key_it_writes(self) -> None:
        out = self._record({}, {"paused": True, "stopped": False}, 4,
                           {"operator": "op", "timestamp": "t", "sig": "ab"})
        assert set(out) == {"paused", "stopped"}
        assert out["paused"] == {
            "version": 4, "delta": {"paused": True, "stopped": False},
            "operator": "op", "timestamp": "t", "sig": "ab",
        }

    def test_an_unsigned_delta_drops_the_keys_it_writes(self) -> None:
        """It must not leave the previous command in place. That signature is
        good and vouches for a value that is no longer there, so the adapter
        would refuse with "not what was signed" — pointing the operator at a
        signature that is fine — instead of "unsigned state key", which is what
        actually happened."""
        before = self._record({}, {"paused": True}, 1,
                              {"operator": "op", "timestamp": "t", "sig": "ab"})
        after = self._record(before, {"paused": False}, 2, None)
        assert after == {}

    def test_it_leaves_other_keys_alone(self) -> None:
        before = self._record({}, {"stopped": True}, 1,
                              {"operator": "op", "timestamp": "t", "sig": "ab"})
        after = self._record(before, {"paused": True}, 2, None)
        assert set(after) == {"stopped"}
