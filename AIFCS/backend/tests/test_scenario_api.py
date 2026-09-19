"""Scenario editing endpoint tests (PHASE 10)."""

from __future__ import annotations

import copy

import pytest


@pytest.fixture
def template(client):
    return client.get("/api/scenarios/template").json()["document"]


def _named(document: dict, name: str) -> dict:
    document = copy.deepcopy(document)
    document["scenario"]["name"] = name
    return document


# ------------------------------------------------------------------- reading


def test_the_catalogue_lists_scenarios_with_their_detail(client):
    body = client.get("/api/scenarios").json()
    assert "demo_alpha" in body["available"]
    assert body["default"] == "demo_alpha"

    demo = next(s for s in body["scenarios"] if s["name"] == "demo_alpha")
    assert demo["readable"] is True
    assert demo["protected"] is True
    assert demo["entity_count"] == 4
    assert set(demo["teams"]) == {"BLUE", "RED"}


def test_one_scenario_comes_back_whole(client):
    body = client.get("/api/scenarios/demo_alpha").json()
    assert body["entity_count"] == 4
    assert body["protected"] is True
    # The document is what the editor edits and what gets written back.
    assert body["document"]["scenario"]["name"] == "demo_alpha"

    wing = next(e for e in body["entities"] if e["id"] == "BLUE-02")
    assert wing["formation_leader"] == "BLUE-01"
    assert wing["waypoints"] == []
    assert wing["route_loop"] is True


def test_a_missing_scenario_is_a_404(client):
    assert client.get("/api/scenarios/ghost").status_code == 404


def test_the_template_is_served_and_valid(client, template):
    assert client.post("/api/scenarios/validate", json=template).json()["valid"] is True


def test_export_returns_the_file_text(client):
    body = client.get("/api/scenarios/demo_alpha/export").json()
    assert body["yaml"].startswith("# DEMO_ALPHA")
    assert client.get("/api/scenarios/ghost/export").status_code == 404


# ---------------------------------------------------------------- validation


def test_validation_reports_the_reason_without_saving(client, template):
    broken = copy.deepcopy(template)
    broken["entities"] = []

    body = client.post("/api/scenarios/validate", json=broken).json()
    assert body["valid"] is False
    assert "at least one entity" in body["error"]
    # Nothing was written.
    assert "new_scenario" not in client.get("/api/scenarios").json()["available"]


def test_validation_names_the_offending_entity(client, template):
    broken = _named(template, "bad_formation")
    broken["entities"].append(
        {
            "id": "BLUE-02",
            "team": "BLUE",
            "position": [0, 0, 6000],
            "agent": "rule",
            "formation": {"leader": "GHOST-99", "offset": [0, 0, 0]},
        }
    )
    body = client.post("/api/scenarios/validate", json=broken).json()
    assert body["valid"] is False
    assert "BLUE-02" in body["error"] and "GHOST-99" in body["error"]


# ------------------------------------------------------------------- writing


def test_create_read_update_delete(client, template):
    document = _named(template, "probe_one")

    created = client.post("/api/scenarios", json=document)
    assert created.status_code == 200
    assert created.json()["name"] == "probe_one"

    document["scenario"]["duration"] = 90.0
    updated = client.put("/api/scenarios/probe_one", json=document)
    assert updated.json()["duration_s"] == 90.0

    assert client.delete("/api/scenarios/probe_one").json()["deleted"] is True
    assert client.get("/api/scenarios/probe_one").status_code == 404


def test_creating_the_same_name_twice_is_refused(client, template):
    document = _named(template, "probe_two")
    assert client.post("/api/scenarios", json=document).status_code == 200
    assert client.post("/api/scenarios", json=document).status_code == 422


def test_an_invalid_document_is_refused_and_not_written(client, template):
    broken = _named(template, "probe_broken")
    broken["entities"] = [{"team": "BLUE"}]  # no id

    assert client.post("/api/scenarios", json=broken).status_code == 422
    assert client.get("/api/scenarios/probe_broken").status_code == 404


def test_updating_something_that_is_not_there_is_a_404(client, template):
    assert client.put("/api/scenarios/ghost", json=_named(template, "ghost")).status_code == 404


@pytest.mark.parametrize("name", ["../escape", "a/b", "con", "with space"])
def test_an_unsafe_name_is_refused(client, template, name):
    assert client.post("/api/scenarios", json=_named(template, name)).status_code == 422


def test_clone(client, template):
    client.post("/api/scenarios", json=_named(template, "probe_src"))

    cloned = client.post("/api/scenarios/probe_src/clone", json={"new_name": "probe_copy"})
    assert cloned.status_code == 200
    assert cloned.json()["name"] == "probe_copy"
    assert client.get("/api/scenarios/probe_copy").json()["name"] == "probe_copy"

    # A second clone to the same name, and a clone of nothing, both refused.
    assert client.post("/api/scenarios/probe_src/clone", json={"new_name": "probe_copy"}).status_code == 422
    assert client.post("/api/scenarios/ghost/clone", json={"new_name": "x"}).status_code == 404
    assert client.post("/api/scenarios/probe_src/clone", json={"new_name": "../x"}).status_code == 422


def test_import_and_export_round_trip(client):
    text = client.get("/api/scenarios/demo_alpha/export").json()["yaml"]

    imported = client.post("/api/scenarios/import", json={"yaml_text": text, "name": "probe_imported"})
    assert imported.status_code == 200
    assert imported.json()["entity_count"] == 4


def test_importing_rubbish_is_refused(client):
    assert client.post("/api/scenarios/import", json={"yaml_text": ": : :"}).status_code == 422
    assert client.post("/api/scenarios/import", json={"yaml_text": "- a\n- b\n"}).status_code == 422


# ------------------------------------------------------------------ refusals


def test_the_default_scenario_cannot_be_deleted(client):
    response = client.delete("/api/scenarios/demo_alpha")
    assert response.status_code == 422
    assert "default scenario" in response.json()["detail"]
    assert "demo_alpha" in client.get("/api/scenarios").json()["available"]


def test_a_scenario_in_use_cannot_be_changed(client, template):
    """Editing the file a run is flying would leave the two disagreeing."""
    client.post("/api/scenarios", json=_named(template, "probe_running"))
    client.post("/api/simulation/start", json={"scenario": "probe_running"})
    try:
        assert client.delete("/api/scenarios/probe_running").status_code == 409
        assert (
            client.put("/api/scenarios/probe_running", json=_named(template, "probe_running")).status_code
            == 409
        )
    finally:
        client.post("/api/simulation/stop")

    # Once stopped, both are allowed again.
    assert (
        client.put("/api/scenarios/probe_running", json=_named(template, "probe_running")).status_code == 200
    )
    assert client.delete("/api/scenarios/probe_running").status_code == 200


def test_a_scenario_made_through_the_api_actually_runs(client, template):
    """The point of the editor: what it writes must fly."""
    document = _named(template, "probe_flyable")
    document["entities"].append(
        {
            "id": "BLUE-02",
            "type": "fictional_aircraft",
            "team": "BLUE",
            "position": [-10600.0, -600.0, 6000.0],
            "velocity": [220.0, 0.0, 0.0],
            "orientation": [0.0, 0.0, 1.5708],
            "controls": {"throttle": 0.26},
            "agent": "rule",
            "formation": {"leader": "BLUE-01", "offset": [-600.0, -600.0, 0.0]},
        }
    )
    assert client.post("/api/scenarios", json=document).status_code == 200

    started = client.post("/api/simulation/start", json={"scenario": "probe_flyable"})
    assert started.status_code == 200
    try:
        client.post("/api/simulation/step", json={"ticks": 1})  # refused while running
        status = client.get("/api/simulation/status").json()
        assert status["scenario"] == "probe_flyable"
        assert status["entity_count"] == 2
        assert status["agent_count"] == 2
    finally:
        client.post("/api/simulation/stop")
