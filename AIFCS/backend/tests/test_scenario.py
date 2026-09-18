"""Scenario loading and validation tests (PHASE 1)."""

from __future__ import annotations

import pytest
import yaml

from core.world_state import Team
from simulation.scenario import (
    ScenarioError,
    find_scenario,
    list_scenarios,
    load_scenario,
    parse_scenario,
)

MINIMAL = {
    "scenario": {"name": "unit_test", "duration": 60},
    "entities": [{"id": "BLUE-01", "team": "BLUE", "position": [0, 0, 5000], "velocity": [100, 0, 0]}],
}


def test_demo_alpha_matches_its_specification(settings):
    scenario = find_scenario(settings.project_root / "scenarios", "demo_alpha")

    assert scenario.name == "demo_alpha"
    assert scenario.seed is not None, "the reference scenario must be reproducible"

    ids = {e.id for e in scenario.entities}
    assert ids == {"BLUE-01", "BLUE-02", "RED-01", "RED-02"}

    teams = {e.team for e in scenario.entities}
    assert teams == {Team.BLUE, Team.RED}


def test_demo_alpha_builds_a_world(settings):
    world = find_scenario(settings.project_root / "scenarios", "demo_alpha").build_world()

    assert len(world.entities) == 4
    assert world.global_status["scenario"] == "demo_alpha"
    assert world.get("BLUE-01").altitude == 6000.0
    assert world.get("RED-01").heading_deg == pytest.approx(270.0)


def test_list_scenarios_finds_demo_alpha(settings):
    assert "demo_alpha" in list_scenarios(settings.project_root / "scenarios")


def test_parse_accepts_a_minimal_scenario():
    scenario = parse_scenario(MINIMAL)
    assert scenario.name == "unit_test"
    assert scenario.duration_s == 60.0
    assert scenario.entities[0].team is Team.BLUE


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"scenario": {"duration": 60}}, "name"),
        ({"scenario": {"name": "x", "duration": -5}}, "duration"),
        ({"entities": []}, "at least one entity"),
        ({"entities": [{"team": "BLUE"}]}, "missing 'id'"),
        ({"entities": [{"id": "A"}, {"id": "A"}]}, "duplicate"),
        ({"entities": [{"id": "A", "team": "PURPLE"}]}, "unknown team"),
        ({"entities": [{"id": "A", "position": [0, 0]}]}, "3 numbers"),
    ],
)
def test_invalid_scenarios_are_rejected(mutation, message):
    document = {**MINIMAL, **mutation}
    with pytest.raises(ScenarioError, match=message):
        parse_scenario(document)


def test_missing_section_is_rejected():
    with pytest.raises(ScenarioError, match="'scenario' section"):
        parse_scenario({"entities": MINIMAL["entities"]})


def test_missing_file_is_reported_clearly(tmp_path):
    with pytest.raises(ScenarioError, match="not found"):
        load_scenario(tmp_path / "nope.yaml")


def test_round_trip_through_yaml(tmp_path):
    path = tmp_path / "round_trip.yaml"
    path.write_text(yaml.safe_dump(MINIMAL), encoding="utf-8")

    scenario = load_scenario(path)
    assert scenario.to_dict()["entity_count"] == 1
