"""World state and entity tests (PHASE 1)."""

from __future__ import annotations

import numpy as np
import pytest

from core.world_state import EntityState, EntityStatus, Team, WorldState


def make_entity(**overrides) -> EntityState:
    defaults = {"id": "BLUE-01", "team": Team.BLUE, "position": [0, 0, 5000], "velocity": [0, 0, 0]}
    return EntityState(**{**defaults, "id": overrides.pop("id", defaults["id"]), **overrides})


def test_altitude_and_speed_are_derived():
    entity = make_entity(position=[100, 200, 6500], velocity=[30, 40, 0])
    assert entity.altitude == 6500.0
    assert entity.speed == pytest.approx(50.0)  # 3-4-5 triangle


@pytest.mark.parametrize(
    ("velocity", "expected"),
    [([0, 100, 0], 0.0), ([100, 0, 0], 90.0), ([0, -100, 0], 180.0), ([-100, 0, 0], 270.0)],
)
def test_heading_uses_compass_convention(velocity, expected):
    assert make_entity(velocity=velocity).heading_deg == pytest.approx(expected)


def test_stationary_entity_reports_yaw_as_heading():
    entity = make_entity(velocity=[0, 0, 0], orientation=[0, 0, np.pi / 2])
    assert entity.heading_deg == pytest.approx(90.0)


def test_rejects_non_finite_vectors():
    with pytest.raises(ValueError, match="finite"):
        make_entity(position=[float("nan"), 0, 0])
    with pytest.raises(ValueError, match="finite"):
        make_entity(velocity=[0, float("inf"), 0])


def test_rejects_empty_entity_id():
    with pytest.raises(ValueError, match="id"):
        EntityState(id="")


def test_copy_does_not_alias_arrays():
    original = make_entity(position=[1, 2, 3])
    duplicate = original.copy()
    duplicate.position[0] = 999.0
    assert original.position[0] == 1.0


def test_duplicate_entity_ids_are_rejected():
    world = WorldState()
    world.add_entity(make_entity(id="BLUE-01"))
    with pytest.raises(ValueError, match="duplicate"):
        world.add_entity(make_entity(id="BLUE-01"))


def test_team_and_active_filters():
    world = WorldState()
    world.add_entity(make_entity(id="BLUE-01", team=Team.BLUE))
    world.add_entity(make_entity(id="RED-01", team=Team.RED))
    disabled = make_entity(id="RED-02", team=Team.RED)
    disabled.status = EntityStatus.DISABLED
    world.add_entity(disabled)

    assert len(world.team_entities(Team.RED)) == 2
    assert {e.id for e in world.active_entities} == {"BLUE-01", "RED-01"}


def test_snapshot_is_fully_independent():
    world = WorldState()
    world.add_entity(make_entity(id="BLUE-01", position=[0, 0, 5000]))

    snapshot = world.snapshot()
    world.get("BLUE-01").position[2] = 9999.0
    world.simulation_time = 42.0

    assert snapshot.get("BLUE-01").altitude == 5000.0
    assert snapshot.simulation_time == 0.0


def test_state_hash_detects_change_and_ignores_ordering():
    def build(order):
        world = WorldState()
        for entity_id in order:
            world.add_entity(make_entity(id=entity_id, position=[0, 0, 5000]))
        return world

    assert build(["A", "B"]).state_hash == build(["B", "A"]).state_hash, (
        "hash must not depend on insertion order"
    )

    moved = build(["A", "B"])
    moved.get("A").position[0] = 1.0
    assert moved.state_hash != build(["A", "B"]).state_hash


def test_to_dict_is_json_serialisable():
    world = WorldState()
    world.add_entity(make_entity(id="BLUE-01", velocity=[220, 0, 0]))

    payload = world.to_dict()
    assert payload["entity_count"] == 1
    entity = payload["entities"][0]
    assert isinstance(entity["position"], list)
    assert entity["heading_deg"] == pytest.approx(90.0)
