"""Simulation control API tests (PHASE 1).

Every endpoint must act on the real engine. These tests fail if any of them
becomes a stub that merely reports success.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def started(client):
    """A client with demo_alpha loaded, stopped and rewound to tick 0.

    start() runs the loop, which advances a few ticks before stop() lands, so
    the reset is what makes each test begin from a known state.
    """
    client.post("/api/simulation/start", json={"scenario": "demo_alpha"})
    client.post("/api/simulation/stop")
    client.post("/api/simulation/reset")
    return client


def test_status_before_a_scenario_is_loaded(client):
    body = client.get("/api/simulation/status").json()
    assert body["scenario_loaded"] is False
    assert body["clock"]["state"] == "STOPPED"
    assert body["clock"]["tick"] == 0


def test_start_loads_the_scenario_and_runs(client):
    response = client.post("/api/simulation/start", json={"scenario": "demo_alpha"})
    assert response.status_code == 200

    body = response.json()
    assert body["scenario"] == "demo_alpha"
    assert body["entity_count"] == 4
    assert body["clock"]["state"] == "RUNNING"

    client.post("/api/simulation/stop")


def test_start_rejects_an_unknown_scenario(client):
    response = client.post("/api/simulation/start", json={"scenario": "does_not_exist"})
    assert response.status_code == 409
    assert "not found" in response.json()["detail"]


def test_step_really_advances_the_world(started):
    before = started.get("/api/simulation/status").json()

    body = started.post("/api/simulation/step", json={"ticks": 120}).json()

    assert body["clock"]["tick"] == before["clock"]["tick"] + 120
    assert body["clock"]["simulation_time"] == pytest.approx(2.0, abs=1e-6)
    assert body["state_hash"] != before["state_hash"], "the world must actually change"


def test_step_moves_entities(started):
    before = started.get("/api/entities").json()["entities"][0]["position"][0]
    started.post("/api/simulation/step", json={"ticks": 600})
    after = started.get("/api/entities").json()["entities"][0]["position"][0]
    assert after != before


def test_pause_and_resume_change_the_clock_state(client):
    client.post("/api/simulation/start", json={"scenario": "demo_alpha"})

    paused = client.post("/api/simulation/pause").json()
    assert paused["clock"]["state"] == "PAUSED"

    resumed = client.post("/api/simulation/resume").json()
    assert resumed["clock"]["state"] == "RUNNING"

    client.post("/api/simulation/stop")


def test_pause_is_refused_when_not_running(started):
    response = started.post("/api/simulation/pause")
    assert response.status_code == 409
    assert "cannot pause" in response.json()["detail"]


def test_reset_returns_the_world_to_its_initial_state(started):
    initial = started.get("/api/simulation/status").json()["state_hash"]
    started.post("/api/simulation/step", json={"ticks": 300})

    body = started.post("/api/simulation/reset").json()

    assert body["clock"]["tick"] == 0
    assert body["clock"]["simulation_time"] == 0.0
    assert body["state_hash"] == initial


def test_speed_endpoint_accepts_allowed_values(started):
    body = started.post("/api/simulation/speed", json={"speed": 10.0}).json()
    assert body["clock"]["speed"] == 10.0
    # dt must not move: speed is pacing only.
    assert body["clock"]["dt"] == pytest.approx(1 / 60)


def test_speed_endpoint_rejects_disallowed_values(started):
    response = started.post("/api/simulation/speed", json={"speed": 3.7})
    assert response.status_code == 409
    assert "not allowed" in response.json()["detail"]


def test_speed_endpoint_validates_the_payload(started):
    assert started.post("/api/simulation/speed", json={"speed": -1}).status_code == 422


def test_world_state_exposes_truth(started):
    started.post("/api/simulation/step", json={"ticks": 60})
    body = started.get("/api/world/state").json()

    assert body["entity_count"] == 4
    assert body["tick"] == 60
    assert len(body["state_hash"]) == 16
    assert body["clock"]["state"] == "STOPPED"


def test_entities_endpoint_returns_fictional_units(started):
    body = started.get("/api/entities").json()
    assert body["count"] == 4

    ids = {e["id"] for e in body["entities"]}
    assert ids == {"BLUE-01", "BLUE-02", "RED-01", "RED-02"}

    for entity in body["entities"]:
        assert entity["team"] in {"BLUE", "RED", "NEUTRAL"}
        assert {"altitude", "speed", "heading_deg", "health", "energy", "fuel"} <= set(entity)


def test_events_endpoint_reports_real_events(client):
    """Uses a raw client: reset deliberately clears the event history."""
    client.post("/api/simulation/start", json={"scenario": "demo_alpha"})
    client.post("/api/simulation/pause")

    body = client.get("/api/events").json()
    types = {e["type"] for e in body["events"]}

    assert "SIMULATION_STARTED" in types
    assert "SIMULATION_PAUSED" in types
    assert "SIMULATION_TICK" not in types, "ticks must stay out of the history"

    client.post("/api/simulation/stop")


def test_reset_clears_the_event_history(started):
    """A reset begins a fresh run; stale events must not leak into it."""
    body = started.get("/api/events").json()
    types = [e["type"] for e in body["events"]]
    assert types == ["SIMULATION_RESET"]


def test_events_endpoint_rejects_an_unknown_type(started):
    response = started.get("/api/events", params={"event_type": "NOT_A_REAL_EVENT"})
    assert response.status_code == 400


def test_scenarios_endpoint_lists_demo_alpha(started):
    body = started.get("/api/scenarios").json()
    assert "demo_alpha" in body["available"]
    assert body["loaded"]["name"] == "demo_alpha"
    assert body["loaded"]["entity_count"] == 4


def test_simulation_subsystem_reports_online(client):
    """PHASE 1 made the engine real and PHASE 2 the physics, so both say ONLINE."""
    states = {s["key"]: s["state"] for s in client.get("/api/system/status").json()["subsystems"]}
    assert states["simulation"] == "ONLINE"
    assert states["physics"] == "ONLINE"
    assert states["agents"] == "ONLINE"
    # Still unbuilt — these must keep reporting honestly.
    assert states["sensors"] == "NOT_IMPLEMENTED"
    assert states["websocket"] == "NOT_IMPLEMENTED"


def test_openapi_documents_the_simulation_endpoints(client):
    paths = client.get("/openapi.json").json()["paths"]
    for path in (
        "/api/simulation/status",
        "/api/simulation/start",
        "/api/simulation/pause",
        "/api/simulation/resume",
        "/api/simulation/reset",
        "/api/simulation/step",
        "/api/simulation/speed",
        "/api/world/state",
        "/api/entities",
        "/api/scenarios",
    ):
        assert path in paths
