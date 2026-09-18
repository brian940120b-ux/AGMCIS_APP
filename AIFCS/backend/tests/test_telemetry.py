"""Telemetry broadcaster and WebSocket tests (PHASE 7)."""

from __future__ import annotations

import asyncio

import pytest

from core.event_bus import EventType
from core.runtime import get_broadcaster
from core.simulation_engine import SimulationEngine
from core.telemetry import TelemetryBroadcaster


class RecordingSink:
    """A stand-in for a WebSocket that keeps what it was sent."""

    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def send_json(self, data: dict) -> None:
        self.frames.append(data)


class BrokenSink:
    """A client whose connection has failed."""

    async def send_json(self, data: dict) -> None:
        raise ConnectionError("client is gone")


@pytest.fixture
def engine(settings) -> SimulationEngine:
    engine = SimulationEngine(settings=settings)
    engine.load_scenario("demo_alpha")
    return engine


# ------------------------------------------------------------------ frames


@pytest.mark.asyncio
async def test_a_new_client_gets_an_immediate_snapshot(engine):
    sink = RecordingSink()
    await TelemetryBroadcaster(engine).connect(sink)

    assert len(sink.frames) == 1
    assert sink.frames[0]["type"] == "snapshot"


@pytest.mark.asyncio
async def test_a_frame_carries_the_full_simulation_picture(engine):
    engine.step(60)
    sink = RecordingSink()
    await TelemetryBroadcaster(engine).connect(sink)

    frame = sink.frames[0]
    assert {
        "clock", "entities", "events", "decisions",
        "controller", "sensors", "communications", "agents", "state_hash",
    } <= set(frame)
    assert len(frame["entities"]) == 4
    assert frame["clock"]["tick"] == 60
    assert frame["scenario"] == "demo_alpha"


@pytest.mark.asyncio
async def test_entities_carry_what_the_dashboard_needs(engine):
    sink = RecordingSink()
    await TelemetryBroadcaster(engine).connect(sink)

    entity = sink.frames[0]["entities"][0]
    assert {"id", "team", "position", "altitude", "speed", "heading_deg", "controls"} <= set(entity)


# ----------------------------------------------------------------- cursors


@pytest.mark.asyncio
async def test_a_client_only_receives_events_after_it_connected(engine):
    engine.step(30)  # produces events before anyone is listening
    broadcaster = TelemetryBroadcaster(engine)
    sink = RecordingSink()
    await broadcaster.connect(sink)

    assert sink.frames[0]["events"] == [], "history is not replayed on connect"

    engine.events.emit(EventType.STATE_CHANGED, message="after connect")
    await broadcaster.broadcast()

    assert [e["message"] for e in sink.frames[1]["events"]] == ["after connect"]


@pytest.mark.asyncio
async def test_events_are_not_sent_twice(engine):
    broadcaster = TelemetryBroadcaster(engine)
    sink = RecordingSink()
    await broadcaster.connect(sink)

    engine.events.emit(EventType.STATE_CHANGED, message="once")
    await broadcaster.broadcast()
    await broadcaster.broadcast()

    assert len(sink.frames[1]["events"]) == 1
    assert sink.frames[2]["events"] == []


@pytest.mark.asyncio
async def test_each_client_has_its_own_cursor(engine):
    broadcaster = TelemetryBroadcaster(engine)
    first = RecordingSink()
    await broadcaster.connect(first)

    engine.events.emit(EventType.STATE_CHANGED, message="before second client")
    await broadcaster.broadcast()

    second = RecordingSink()
    await broadcaster.connect(second)

    assert len(first.frames[1]["events"]) == 1
    assert second.frames[0]["events"] == [], "a late client does not get the backlog"


@pytest.mark.asyncio
async def test_decisions_stream_incrementally(engine):
    broadcaster = TelemetryBroadcaster(engine)
    sink = RecordingSink()
    await broadcaster.connect(sink)

    engine.step(60)  # agents decide ten times a second
    await broadcaster.broadcast()

    decisions = sink.frames[1]["decisions"]
    assert decisions, "decisions made after connecting should arrive"
    assert all(d["reason_codes"] for d in decisions), "each still carries its evidence"

    await broadcaster.broadcast()
    assert sink.frames[2]["decisions"] == []


# ------------------------------------------------------------- connections


@pytest.mark.asyncio
async def test_multiple_clients_all_receive_frames(engine):
    broadcaster = TelemetryBroadcaster(engine)
    sinks = [RecordingSink() for _ in range(3)]
    for sink in sinks:
        await broadcaster.connect(sink)

    assert broadcaster.subscriber_count == 3
    assert await broadcaster.broadcast() == 3
    assert all(len(sink.frames) == 2 for sink in sinks)


@pytest.mark.asyncio
async def test_a_failing_client_is_dropped_without_affecting_the_others(engine):
    broadcaster = TelemetryBroadcaster(engine)
    healthy = RecordingSink()
    await broadcaster.connect(healthy)
    await broadcaster.connect(BrokenSink())

    assert broadcaster.subscriber_count == 1, "the broken client fails on its first frame"

    await broadcaster.broadcast()
    assert len(healthy.frames) == 2


@pytest.mark.asyncio
async def test_disconnecting_stops_delivery(engine):
    broadcaster = TelemetryBroadcaster(engine)
    sink = RecordingSink()
    subscriber = await broadcaster.connect(sink)

    broadcaster.disconnect(subscriber)
    await broadcaster.broadcast()

    assert len(sink.frames) == 1
    assert broadcaster.subscriber_count == 0


@pytest.mark.asyncio
async def test_disconnecting_twice_is_harmless(engine):
    broadcaster = TelemetryBroadcaster(engine)
    subscriber = await broadcaster.connect(RecordingSink())
    broadcaster.disconnect(subscriber)
    broadcaster.disconnect(subscriber)
    assert broadcaster.subscriber_count == 0


# ------------------------------------------------------------------- timing


@pytest.mark.asyncio
async def test_the_broadcaster_pushes_at_its_configured_rate(engine):
    """Telemetry runs on its own clock, not the physics tick."""
    broadcaster = TelemetryBroadcaster(engine, broadcast_rate_hz=50.0)
    sink = RecordingSink()
    await broadcaster.connect(sink)

    broadcaster.start()
    await asyncio.sleep(0.4)
    await broadcaster.stop()

    # 50 Hz for 0.4 s is about 20 frames; allow generous slack for scheduling.
    assert 8 <= len(sink.frames) - 1 <= 32, f"got {len(sink.frames) - 1} frames"


@pytest.mark.asyncio
async def test_stopping_is_idempotent(engine):
    broadcaster = TelemetryBroadcaster(engine)
    broadcaster.start()
    await broadcaster.stop()
    await broadcaster.stop()
    assert broadcaster.status()["running"] is False


@pytest.mark.asyncio
async def test_status_reports_the_broadcaster_state(engine):
    broadcaster = TelemetryBroadcaster(engine, broadcast_rate_hz=20.0)
    await broadcaster.connect(RecordingSink())

    status = broadcaster.status()
    assert status["broadcast_rate_hz"] == 20.0
    assert status["subscribers"] == 1


# -------------------------------------------------------------- websocket


def test_the_websocket_streams_live_telemetry(client):
    """End to end through the real ASGI stack."""
    client.post("/api/simulation/start", json={"scenario": "demo_alpha"})

    with client.websocket_connect("/ws/simulation") as websocket:
        snapshot = websocket.receive_json()
        assert snapshot["type"] == "snapshot"
        assert len(snapshot["entities"]) == 4
        assert snapshot["clock"]["state"] in {"RUNNING", "PAUSED", "STOPPED"}

        frame = websocket.receive_json()
        assert frame["type"] == "telemetry"
        assert frame["sequence"] >= 1

    client.post("/api/simulation/stop")


def test_the_websocket_reflects_a_running_simulation(client):
    client.post("/api/simulation/start", json={"scenario": "demo_alpha"})

    with client.websocket_connect("/ws/simulation") as websocket:
        first = websocket.receive_json()
        ticks = [first["clock"]["tick"]]
        for _ in range(4):
            ticks.append(websocket.receive_json()["clock"]["tick"])

    client.post("/api/simulation/stop")
    assert ticks[-1] > ticks[0], f"the clock should advance across frames: {ticks}"


def test_the_telemetry_status_endpoint_reports_the_broadcaster(client):
    body = client.get("/api/telemetry").json()
    assert body["broadcast_rate_hz"] > 0
    assert "subscribers" in body


def test_the_websocket_subsystem_reports_online(client):
    states = {s["key"]: s["state"] for s in client.get("/api/system/status").json()["subsystems"]}
    assert states["websocket"] == "ONLINE"


def test_the_broadcaster_singleton_uses_the_shared_engine():
    assert get_broadcaster().engine is get_broadcaster().engine
