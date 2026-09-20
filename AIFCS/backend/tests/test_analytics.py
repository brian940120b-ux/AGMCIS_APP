"""Analytics tests (PHASE 17).

Two things worth guarding. The first is that **a run with nothing stored says
so** rather than being drawn as a flat line at zero — an empty chart is a claim
about the run, and this platform does not make claims it cannot support. The
second is that **analysis cannot reach a tick**: the series layer takes rows and
returns numbers, so adding a chart can never change how an aircraft flies.
"""

from __future__ import annotations

import time

from analytics.series import (
    BEHAVIOUR_ORDER,
    behaviour_shares,
    coordination_chart,
    flight_charts,
    score_matrix,
    survival_chart,
)


def _record_a_run(client, seconds: float = 2.5) -> str:
    assert client.post("/api/simulation/start", json={"scenario": "demo_alpha"}).status_code == 200
    run_id = client.get("/api/runs/current").json()["run_id"]
    time.sleep(seconds)
    assert client.post("/api/simulation/stop").status_code == 200
    return run_id


def _samples() -> list[dict]:
    """Two units, two teams, three seconds apart."""
    rows = []
    for index, at in enumerate((0.0, 1.0, 2.0)):
        for entity_id, altitude in (("BLUE-01", 6000.0 + index), ("RED-01", 7000.0 - index)):
            rows.append(
                {
                    "entity_id": entity_id,
                    "simulation_time": at,
                    "tick": index * 60,
                    "altitude_m": altitude,
                    "speed_mps": 220.0 + index,
                    "status": "ACTIVE",
                }
            )
    return rows


# -------------------------------------------------------------- series layer


def test_flight_charts_group_units_by_team():
    altitude, speed = flight_charts(_samples())
    assert altitude.available and speed.available
    assert [s.key for s in altitude.series] == ["BLUE-01", "RED-01"]
    assert [s.group for s in altitude.series] == ["BLUE", "RED"]
    assert altitude.series[0].points == [[0.0, 6000.0], [1.0, 6001.0], [2.0, 6002.0]]


def test_a_run_with_no_samples_says_so_instead_of_charting_zero():
    """A flat line at zero is a claim about the run, not an absence of data."""
    altitude, speed = flight_charts([])
    for chart in (altitude, speed):
        assert chart.available is False
        assert "stored no telemetry" in chart.detail
        assert chart.series == []

    assert survival_chart([]).available is False
    assert coordination_chart([]).available is False
    assert score_matrix([]).available is False
    assert behaviour_shares([]).available is False


def test_survival_counts_only_units_still_active():
    rows = _samples()
    for row in rows:
        if row["entity_id"] == "RED-01" and row["simulation_time"] == 2.0:
            row["status"] = "DISABLED"

    chart = survival_chart(rows)
    blue = next(s for s in chart.series if s.key == "BLUE")
    red = next(s for s in chart.series if s.key == "RED")
    assert [p[1] for p in blue.points] == [1.0, 1.0, 1.0]
    assert [p[1] for p in red.points] == [1.0, 1.0, 0.0]


def test_coordination_counts_units_that_decided_formation():
    decisions = [
        {"entity_id": "BLUE-01", "simulation_time": 0.0, "behaviour": "PATROL"},
        {"entity_id": "BLUE-02", "simulation_time": 0.5, "behaviour": "FORMATION"},
        {"entity_id": "RED-02", "simulation_time": 1.0, "behaviour": "FORMATION"},
        # Same unit again in the same bucket must not count twice.
        {"entity_id": "BLUE-02", "simulation_time": 2.0, "behaviour": "FORMATION"},
    ]
    chart = coordination_chart(decisions, bucket_s=5.0)
    blue = next(s for s in chart.series if s.key == "BLUE")
    assert blue.points == [[0.0, 1.0]]


def test_behaviour_shares_are_a_share_of_each_unit_s_own_decisions():
    decisions = [
        {"entity_id": "BLUE-01", "simulation_time": 0.0, "behaviour": "PATROL"},
        {"entity_id": "BLUE-01", "simulation_time": 0.1, "behaviour": "PATROL"},
        {"entity_id": "BLUE-01", "simulation_time": 0.2, "behaviour": "FORMATION"},
        {"entity_id": "RED-01", "simulation_time": 0.0, "behaviour": "AVOID"},
    ]
    shares = behaviour_shares(decisions)
    assert shares.counts == {"BLUE-01": 3, "RED-01": 1}
    assert shares.shares["BLUE-01"]["PATROL"] == 0.6667
    assert shares.shares["RED-01"]["AVOID"] == 1.0
    # A behaviour another unit used still appears, at zero, so the stacks line up.
    assert shares.shares["RED-01"]["PATROL"] == 0.0


def test_behaviours_come_back_in_the_validated_stacking_order():
    """The palette's colour-vision check is on adjacent pairs.

    Reordering the stack would put a different pair next to each other and void
    the separation the palette was chosen for, so the order is part of the
    contract rather than a presentation detail.
    """
    decisions = [
        {"entity_id": "BLUE-01", "simulation_time": 0.0, "behaviour": b} for b in reversed(BEHAVIOUR_ORDER)
    ]
    assert behaviour_shares(decisions).behaviours == list(BEHAVIOUR_ORDER)


def test_a_term_that_did_not_apply_is_not_a_zero_score():
    """A unit with no route cannot be marked down for navigation it never had."""
    scores = [
        {
            "subject": "entity",
            "subject_id": "BLUE-02",
            "total": 70.0,
            "breakdown": {
                "terms": [
                    {
                        "name": "survival",
                        "fraction": 1.0,
                        "weight": 30.0,
                        "points": 30.0,
                        "applicable": True,
                    },
                    {
                        "name": "navigation",
                        "fraction": 0.0,
                        "weight": 25.0,
                        "points": 0.0,
                        "applicable": False,
                    },
                ]
            },
        }
    ]
    matrix = score_matrix(scores)
    assert matrix.available is True
    assert matrix.entities == ["BLUE-02"]
    assert matrix.terms == ["survival", "navigation"]
    assert matrix.cells["BLUE-02"]["navigation"]["applicable"] == 0.0
    assert matrix.cells["BLUE-02"]["survival"]["applicable"] == 1.0


def test_team_scores_are_left_out_of_the_per_unit_matrix():
    scores = [{"subject": "team", "subject_id": "BLUE", "total": 80.0, "breakdown": {}}]
    assert score_matrix(scores).available is False


# ---------------------------------------------------------------------- API


def test_analytics_returns_every_chart_for_a_real_run(client):
    run_id = _record_a_run(client)
    body = client.get(f"/api/analytics/runs/{run_id}").json()

    keys = [c["key"] for c in body["charts"]]
    assert keys == ["altitude", "speed", "survival", "coordination"]
    assert body["run"]["run_id"] == run_id
    assert body["sampled"]["decisions"] > 0
    assert "fictional" in body["notice"].lower()

    altitude = body["charts"][0]
    assert altitude["available"] is True
    assert {s["group"] for s in altitude["series"]} == {"BLUE", "RED"}
    # Eight units would need eight hues; identity rides the label instead.
    assert all(s["label"] == s["key"] for s in altitude["series"])


def test_analytics_for_an_unknown_run_is_a_404(client):
    assert client.get("/api/analytics/runs/not-a-run").status_code == 404


def test_comparing_fewer_than_two_runs_is_refused(client):
    response = client.get("/api/analytics/compare", params={"runs": "only-one"})
    assert response.status_code == 400
    assert "at least two" in response.json()["detail"]


def test_comparing_puts_two_real_runs_side_by_side(client):
    first = _record_a_run(client)
    second = _record_a_run(client)

    body = client.get("/api/analytics/compare", params={"runs": f"{first},{second}"}).json()
    assert body["count"] == 2
    assert [r["run_id"] for r in body["runs"]] == [first, second]
    # Both were judged by the same weights, so the totals mean the same thing.
    assert body["comparable"] is True
    for run in body["runs"]:
        assert run["scenario"] == "demo_alpha"
        assert run["integrator"]


def test_a_comparison_says_when_runs_were_judged_differently(client, monkeypatch):
    """Two runs scored against different weights are not comparable totals."""
    first = _record_a_run(client)
    second = _record_a_run(client)

    from core.runtime import get_run_manager

    repository = get_run_manager().repository
    assert repository is not None
    # Re-stamp one run's scores as if the weights had been changed between them.
    # Committed explicitly: connections are per thread, and the request handler
    # reads on a different one.
    repository.db.execute("UPDATE scores SET weights_hash = ? WHERE run_id = ?", ("changed", second))
    repository.db.connection.commit()

    body = client.get("/api/analytics/compare", params={"runs": f"{first},{second}"}).json()
    assert body["comparable"] is False
    assert "not comparable" in body["detail"]


def test_analysis_cannot_reach_the_engine():
    """The series layer takes rows and returns numbers, and that is all.

    Importing it must not pull in the simulation engine — that separation is
    what makes it safe to add a chart without any risk to how an aircraft flies.
    """
    import inspect

    import analytics.series as series

    source = inspect.getsource(series)
    for forbidden in ("simulation_engine", "WorldState", "get_engine", "EntityState"):
        assert forbidden not in source, f"the analytics layer must not reach {forbidden}"
