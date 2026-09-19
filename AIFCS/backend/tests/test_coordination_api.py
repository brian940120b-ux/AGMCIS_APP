"""Team, task and commander endpoint tests (PHASE 14-15)."""

from __future__ import annotations

import time


def _run(client, seconds: float = 3.0, scenario: str = "demo_alpha") -> None:
    client.post("/api/simulation/start", json={"scenario": scenario})
    time.sleep(seconds)
    client.post("/api/simulation/pause")


def test_teams_are_listed_with_their_own_picture(client):
    _run(client)
    body = client.get("/api/teams").json()

    assert body["count"] == 2
    names = {t["team"] for t in body["teams"]}
    assert names == {"BLUE", "RED"}
    for team in body["teams"]:
        assert 0.0 <= team["coverage"] <= 1.0
        assert len(team["members"]) == team["size"]


def test_a_team_detail_includes_what_each_unit_was_told(client):
    _run(client)
    body = client.get("/api/teams/blue").json()

    assert body["team"] == "BLUE"
    assert set(body["tasks"]) == {m["entity_id"] for m in body["members"]}
    assert any(task is not None for task in body["tasks"].values())


def test_an_unknown_team_is_a_404(client):
    assert client.get("/api/teams/purple").status_code == 404


def test_tasks_report_what_was_allocated_and_why(client):
    _run(client)
    body = client.get("/api/tasks").json()

    assert body["issued"] > 0
    assert body["applied"] > 0
    assert body["active"]
    for task in body["active"].values():
        assert task["reasons"], "every allocation must say why it was made"


def test_one_unit_can_be_asked_about(client):
    _run(client)
    body = client.get("/api/tasks/BLUE-02").json()

    assert body["entity_id"] == "BLUE-02"
    assert body["active"] is not None
    assert body["active"]["type"] in {"PATROL", "ESCORT", "TRANSIT", "HOLD"}
    assert client.get("/api/tasks/GHOST-99").status_code == 404


def test_commanders_report_that_they_do_not_write_controls(client):
    _run(client)
    body = client.get("/api/commanders").json()

    assert body["enabled"] is True
    assert body["count"] == 2
    for commander in body["commanders"]:
        assert commander["writes_controls"] is False
        assert commander["decision_count"] > 0
    assert "no code path" in body["notice"]


def test_eight_agents_are_coordinated_through_the_api(client):
    _run(client, seconds=4.0, scenario="team_eight")

    teams = client.get("/api/teams").json()
    assert sum(t["active"] for t in teams["teams"]) == 8

    tasks = client.get("/api/tasks").json()
    assert len(tasks["active"]) == 8
    kinds = [t["type"] for t in tasks["active"].values()]
    assert kinds.count("PATROL") == 4
    assert kinds.count("ESCORT") == 4


def test_the_simulation_status_carries_the_coordination_layer(client):
    _run(client)
    status = client.get("/api/simulation/status").json()

    assert "tasks" in status and "teams" in status and "commanders" in status
    assert status["messages"]["delivered_by_type"].get("TASK_ORDER", 0) > 0
