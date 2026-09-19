"""Replay, run history and scoring endpoint tests (PHASE 9)."""

from __future__ import annotations

import time


def _record_a_run(client, seconds: float = 2.0) -> str:
    """Start a real run through the API, let it record, then stop it."""
    assert client.post("/api/simulation/start", json={"scenario": "demo_alpha"}).status_code == 200
    run_id = client.get("/api/runs/current").json()["run_id"]
    time.sleep(seconds)
    assert client.post("/api/simulation/stop").status_code == 200
    return run_id


# ------------------------------------------------------------------ recording


def test_starting_a_simulation_starts_a_recording(client):
    client.post("/api/simulation/start", json={"scenario": "demo_alpha"})
    current = client.get("/api/runs/current").json()
    assert current["active"] is True
    assert current["run_id"]
    assert current["recording"] is True
    client.post("/api/simulation/stop")


def test_stopping_closes_scores_and_stores_the_run(client):
    run_id = _record_a_run(client)

    current = client.get("/api/runs/current").json()
    assert current["active"] is False

    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["end_reason"] == "stopped by operator"
    assert detail["ticks"] > 0
    assert detail["final_state_hash"]
    assert len(detail["entities"]) == 4
    assert detail["replay"]["frames"] > 0
    assert len(detail["scores"]) > 0


def test_resetting_closes_the_run_before_rebuilding_the_world(client):
    """After a reset the world is the scenario's initial state, which is not
    how the run ended — so the run must be closed first."""
    client.post("/api/simulation/start", json={"scenario": "demo_alpha"})
    run_id = client.get("/api/runs/current").json()["run_id"]
    time.sleep(1.0)
    client.post("/api/simulation/reset")

    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["end_reason"] == "reset by operator"
    assert detail["ticks"] > 0


def test_recordings_are_listed(client):
    run_id = _record_a_run(client)
    body = client.get("/api/replay/recordings").json()

    assert body["count"] >= 1
    assert any(r["run_id"] == run_id and r["readable"] for r in body["recordings"])


# ----------------------------------------------------------------- transport


def test_transport_controls_move_the_real_cursor(client):
    run_id = _record_a_run(client)

    status = client.post("/api/replay/load", json={"run_id": run_id}).json()
    assert status["loaded"] is True
    assert status["frame_count"] > 1

    assert client.post("/api/replay/seek", json={"frame": 5}).json()["frame_index"] == 5
    assert client.post("/api/replay/step", json={"frames": -2}).json()["frame_index"] == 3
    assert client.post("/api/replay/speed", json={"speed": 2.0}).json()["speed"] == 2.0

    frame = client.get("/api/replay/frame").json()["frame"]
    assert frame["kind"] == "frame"
    assert len(frame["entities"]) == 4

    assert client.post("/api/replay/unload").json()["loaded"] is False


def test_transport_refuses_when_nothing_is_loaded(client):
    assert client.get("/api/replay/frame").status_code == 409
    assert client.post("/api/replay/play").status_code == 409
    assert client.post("/api/replay/pause").status_code == 409
    assert client.get("/api/replay/events").status_code == 409


def test_an_unknown_speed_is_refused(client):
    run_id = _record_a_run(client)
    client.post("/api/replay/load", json={"run_id": run_id})
    assert client.post("/api/replay/speed", json={"speed": 3.7}).status_code == 409


def test_seek_requires_a_target(client):
    run_id = _record_a_run(client)
    client.post("/api/replay/load", json={"run_id": run_id})
    assert client.post("/api/replay/seek", json={}).status_code == 400


def test_loading_a_missing_recording_is_a_404(client):
    assert client.post("/api/replay/load", json={"run_id": "nope"}).status_code == 404
    assert client.post("/api/replay/load", json={}).status_code == 400


def test_a_path_outside_the_replay_directory_is_refused(client):
    """Without this check a path parameter would read any file on the machine."""
    for attempt in ("../../etc/passwd", "/etc/passwd", "../configs/simulation.yaml"):
        response = client.post("/api/replay/load", json={"path": attempt})
        assert response.status_code in (400, 404), attempt
        if response.status_code == 400:
            assert "replay directory" in response.json()["detail"]


# ------------------------------------------------------------------- history


def test_run_history_and_its_detail_views(client):
    run_id = _record_a_run(client)

    listing = client.get("/api/runs").json()
    assert listing["count"] >= 1
    assert any(r["run_id"] == run_id for r in listing["runs"])

    assert client.get(f"/api/runs/{run_id}/decisions").json()["count"] > 0
    assert client.get(f"/api/runs/{run_id}/events").json()["count"] > 0
    assert client.get(f"/api/runs/{run_id}/telemetry").json()["count"] > 0
    assert client.get("/api/runs/nope").status_code == 404


def test_deleting_a_run_removes_its_rows_and_its_file(client):
    run_id = _record_a_run(client)

    body = client.delete(f"/api/runs/{run_id}").json()
    assert body["rows_deleted"] is True
    assert body["recording_deleted"] is True

    assert client.get(f"/api/runs/{run_id}").status_code == 404
    listed = client.get("/api/replay/recordings").json()["recordings"]
    assert all(r["run_id"] != run_id for r in listed)
    assert client.delete(f"/api/runs/{run_id}").status_code == 404


def test_a_recording_run_cannot_be_deleted(client):
    client.post("/api/simulation/start", json={"scenario": "demo_alpha"})
    run_id = client.get("/api/runs/current").json()["run_id"]
    try:
        assert client.delete(f"/api/runs/{run_id}").status_code == 409
    finally:
        client.post("/api/simulation/stop")


# ------------------------------------------------------------------- scoring


def test_the_scoring_ruler_is_published(client):
    body = client.get("/api/scoring/weights").json()
    assert body["max_points"] > 0
    assert body["weights_hash"]
    assert set(body["weights"]) == {
        "survival",
        "navigation",
        "formation",
        "safety",
        "efficiency",
        "information",
    }
    assert "no weapon" in body["notice"]


def test_a_run_can_be_rescored_from_its_recording(client):
    run_id = _record_a_run(client)

    result = client.post(f"/api/runs/{run_id}/score").json()
    assert result["run_id"] == run_id
    assert len(result["entities"]) == 4
    assert result["teams"]
    assert all(t["detail"] for e in result["entities"] for t in e["terms"])

    stored = client.get(f"/api/runs/{run_id}").json()["scores"]
    assert any(s["subject"] == "team" for s in stored)


def test_rescoring_a_run_with_no_recording_is_a_404(client):
    assert client.post("/api/runs/ghost/score").status_code == 404
