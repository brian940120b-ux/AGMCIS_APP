"""Sensor model tests (PHASE 5).

The contract being checked: an agent receives an *estimate*, never the truth,
and every degradation is reproducible from the run seed.
"""

from __future__ import annotations

import numpy as np
import pytest

from core.simulation_engine import SimulationEngine
from core.world_state import EntityState, EntityStatus, Team, WorldState
from simulation.sensors import SensorConfig, SensorModel

PERFECT = SensorConfig(
    # Explicitly all-round: the platform default is narrower, and these tests
    # isolate the other degradations rather than the detection envelope.
    field_of_regard_deg=180.0,
    latency_s=0.0,
    dropout_probability=0.0,
    position_noise_base_m=0.0,
    position_noise_per_km_m=0.0,
    velocity_noise_mps=0.0,
    ownship_position_noise_m=0.0,
    ownship_velocity_noise_mps=0.0,
)


def make_world(*offsets: tuple[str, Team, tuple[float, float, float]]) -> WorldState:
    world = WorldState()
    world.add_entity(
        EntityState(
            id="BLUE-01",
            team=Team.BLUE,
            position=np.array([0.0, 0.0, 6000.0]),
            velocity=np.array([220.0, 0.0, 0.0]),
            orientation=np.array([0.0, 0.0, np.pi / 2]),  # nose east
        )
    )
    for entity_id, team, offset in offsets:
        world.add_entity(
            EntityState(
                id=entity_id,
                team=team,
                position=np.array(offset),
                velocity=np.array([0.0, 0.0, 0.0]),
                orientation=np.array([0.0, 0.0, np.pi / 2]),
            )
        )
    return world


def observe(model: SensorModel, world: WorldState, observer_id: str = "BLUE-01"):
    model.record(world)
    return model.observe(f"AGENT-{observer_id}", world.entities[observer_id], world)


# ------------------------------------------------------------------ detection


def test_a_contact_in_range_is_reported():
    world = make_world(("RED-01", Team.RED, (10_000.0, 0.0, 6000.0)))
    observation = observe(SensorModel(PERFECT, seed=1), world)

    assert [c.entity_id for c in observation.contacts] == ["RED-01"]
    assert observation.contacts[0].distance_m == pytest.approx(10_000.0, abs=1.0)


def test_a_contact_beyond_range_is_simply_absent():
    """The agent is not told that something is out there but unseen."""
    world = make_world(("RED-01", Team.RED, (200_000.0, 0.0, 6000.0)))
    config = SensorConfig(max_range_m=80_000.0, dropout_probability=0.0, latency_s=0.0)
    observation = observe(SensorModel(config, seed=1), world)

    assert observation.contacts == []


def test_a_contact_outside_the_field_of_regard_is_not_seen():
    # Nose is east; the contact is due west, 180 degrees off.
    world = make_world(("RED-01", Team.RED, (-10_000.0, 0.0, 6000.0)))
    config = SensorConfig(
        field_of_regard_deg=90.0,
        dropout_probability=0.0,
        latency_s=0.0,
        position_noise_base_m=0.0,
        position_noise_per_km_m=0.0,
    )
    observation = observe(SensorModel(config, seed=1), world)

    assert observation.contacts == []


def test_an_all_round_sensor_sees_behind():
    world = make_world(("RED-01", Team.RED, (-10_000.0, 0.0, 6000.0)))
    observation = observe(SensorModel(PERFECT, seed=1), world)
    assert [c.entity_id for c in observation.contacts] == ["RED-01"]


def test_inactive_entities_are_not_detected():
    world = make_world(("RED-01", Team.RED, (10_000.0, 0.0, 6000.0)))
    world.get("RED-01").status = EntityStatus.DISABLED
    assert observe(SensorModel(PERFECT, seed=1), world).contacts == []


def test_the_observer_never_sees_itself():
    world = make_world(("RED-01", Team.RED, (10_000.0, 0.0, 6000.0)))
    observation = observe(SensorModel(PERFECT, seed=1), world)
    assert all(c.entity_id != "BLUE-01" for c in observation.contacts)


def test_friend_and_foe_are_distinguished():
    world = make_world(
        ("BLUE-02", Team.BLUE, (-800.0, -800.0, 6000.0)),
        ("RED-01", Team.RED, (20_000.0, 0.0, 6000.0)),
    )
    contacts = {c.entity_id: c for c in observe(SensorModel(PERFECT, seed=1), world).contacts}

    assert contacts["BLUE-02"].is_friendly is True
    assert contacts["RED-01"].is_friendly is False


# --------------------------------------------------------------------- noise


def test_measurements_carry_noise():
    world = make_world(("RED-01", Team.RED, (10_000.0, 0.0, 6000.0)))
    config = SensorConfig(latency_s=0.0, dropout_probability=0.0, position_noise_base_m=50.0)
    observation = observe(SensorModel(config, seed=7), world)

    reported = observation.contacts[0].distance_m
    assert reported != pytest.approx(10_000.0, abs=1e-9), "a measurement must not be exact"
    assert reported == pytest.approx(10_000.0, abs=500.0), "but it must still be usable"


def test_noise_grows_with_range():
    def spread(distance_m: float) -> float:
        errors = []
        for seed in range(40):
            world = make_world(("RED-01", Team.RED, (distance_m, 0.0, 6000.0)))
            config = SensorConfig(
                latency_s=0.0,
                dropout_probability=0.0,
                position_noise_base_m=10.0,
                position_noise_per_km_m=5.0,
                ownship_position_noise_m=0.0,
                ownship_velocity_noise_mps=0.0,
            )
            observation = observe(SensorModel(config, seed=seed), world)
            errors.append(abs(observation.contacts[0].distance_m - distance_m))
        return float(np.mean(errors))

    assert spread(60_000.0) > spread(5_000.0) * 2


def test_the_ownship_estimate_is_also_imperfect():
    world = make_world()
    config = SensorConfig(latency_s=0.0, ownship_position_noise_m=10.0)
    observation = observe(SensorModel(config, seed=3), world)

    truth = world.get("BLUE-01").position
    assert not np.allclose(observation.position, truth), "ownship state is estimated too"
    assert float(np.linalg.norm(observation.position - truth)) < 100.0


# --------------------------------------------------------------------- delay


def test_reported_contacts_are_delayed():
    """What is reported is where the contact was, one latency ago."""
    config = SensorConfig(
        latency_s=0.5,
        dropout_probability=0.0,
        position_noise_base_m=0.0,
        position_noise_per_km_m=0.0,
        velocity_noise_mps=0.0,
        ownship_position_noise_m=0.0,
        ownship_velocity_noise_mps=0.0,
    )
    model = SensorModel(config, seed=1, tick_rate_hz=60)

    world = make_world(("RED-01", Team.RED, (10_000.0, 0.0, 6000.0)))
    target = world.get("RED-01")

    # Fill the latency buffer while the target moves steadily east.
    for _ in range(60):
        model.record(world)
        target.position[0] += 100.0

    observation = model.observe("AGENT-BLUE-01", world.get("BLUE-01"), world)
    reported_x = observation.contacts[0].relative_position[0]

    assert reported_x < target.position[0], "the report must lag the truth"


# ------------------------------------------------------------------- dropout


def test_dropout_loses_detections():
    world = make_world(("RED-01", Team.RED, (10_000.0, 0.0, 6000.0)))
    config = SensorConfig(
        latency_s=0.0,
        dropout_probability=1.0,
        track_memory_s=0.0,
        position_noise_base_m=0.0,
        position_noise_per_km_m=0.0,
    )
    model = SensorModel(config, seed=1)
    assert observe(model, world).contacts == []


def test_a_dropped_contact_is_coasted_then_forgotten():
    world = make_world(("RED-01", Team.RED, (10_000.0, 0.0, 6000.0)))
    model = SensorModel(SensorConfig(latency_s=0.0, dropout_probability=0.0, track_memory_s=2.0), seed=1)
    observe(model, world)  # establish the track

    # Now make every detection fail.
    model.config = SensorConfig(latency_s=0.0, dropout_probability=1.0, track_memory_s=2.0)

    world.simulation_time = 1.0
    coasted = observe(model, world)
    assert len(coasted.contacts) == 1
    assert coasted.contacts[0].measured is False
    assert coasted.contacts[0].age_s == pytest.approx(1.0)

    world.simulation_time = 5.0  # beyond track memory
    assert observe(model, world).contacts == []


def test_a_coasted_track_is_dead_reckoned_forward():
    world = WorldState()
    world.add_entity(
        EntityState(
            id="BLUE-01",
            team=Team.BLUE,
            position=np.array([0.0, 0.0, 6000.0]),
            velocity=np.zeros(3),
            orientation=np.array([0.0, 0.0, np.pi / 2]),
        )
    )
    world.add_entity(
        EntityState(
            id="RED-01",
            team=Team.RED,
            position=np.array([10_000.0, 0.0, 6000.0]),
            velocity=np.array([100.0, 0.0, 0.0]),
            orientation=np.array([0.0, 0.0, np.pi / 2]),
        )
    )
    model = SensorModel(
        SensorConfig(
            latency_s=0.0,
            dropout_probability=0.0,
            track_memory_s=5.0,
            position_noise_base_m=0.0,
            position_noise_per_km_m=0.0,
            velocity_noise_mps=0.0,
            ownship_position_noise_m=0.0,
            ownship_velocity_noise_mps=0.0,
        ),
        seed=1,
    )
    observe(model, world)

    model.config = SensorConfig(
        latency_s=0.0,
        dropout_probability=1.0,
        track_memory_s=5.0,
        ownship_position_noise_m=0.0,
        ownship_velocity_noise_mps=0.0,
    )
    world.simulation_time = 2.0
    contact = observe(model, world).contacts[0]

    # Two seconds at 100 m/s means the estimate should have advanced ~200 m.
    assert contact.relative_position[0] == pytest.approx(10_200.0, abs=1.0)


# ---------------------------------------------------------------- confidence


def test_confidence_falls_with_range():
    def confidence_at(distance_m: float) -> float:
        world = make_world(("RED-01", Team.RED, (distance_m, 0.0, 6000.0)))
        return observe(SensorModel(PERFECT, seed=1), world).contacts[0].confidence

    assert confidence_at(5_000.0) > confidence_at(70_000.0)


def test_confidence_falls_as_a_track_goes_stale():
    world = make_world(("RED-01", Team.RED, (10_000.0, 0.0, 6000.0)))
    model = SensorModel(SensorConfig(latency_s=0.0, dropout_probability=0.0, track_memory_s=4.0), seed=1)
    fresh = observe(model, world).contacts[0].confidence

    model.config = SensorConfig(latency_s=0.0, dropout_probability=1.0, track_memory_s=4.0)
    world.simulation_time = 3.0
    stale = observe(model, world).contacts[0].confidence

    assert stale < fresh


def test_confidence_stays_within_bounds():
    world = make_world(("RED-01", Team.RED, (79_000.0, 0.0, 6000.0)))
    confidence = observe(SensorModel(PERFECT, seed=1), world).contacts[0].confidence
    assert 0.0 <= confidence <= 1.0


# ------------------------------------------------------------- reproducibility


def test_the_same_seed_reproduces_the_same_measurements():
    def measure(seed: int) -> list[float]:
        world = make_world(("RED-01", Team.RED, (10_000.0, 0.0, 6000.0)))
        model = SensorModel(SensorConfig(latency_s=0.0), seed=seed)
        return [observe(model, world).contacts[0].distance_m for _ in range(5)]

    assert measure(11) == measure(11)
    assert measure(11) != measure(12)


def test_reset_restarts_the_noise_sequence():
    world = make_world(("RED-01", Team.RED, (10_000.0, 0.0, 6000.0)))
    model = SensorModel(SensorConfig(latency_s=0.0), seed=5)

    first = observe(model, world).contacts[0].distance_m
    model.reset()
    assert observe(model, world).contacts[0].distance_m == pytest.approx(first)


# -------------------------------------------------------------- disabled mode


def test_disabling_the_sensor_gives_perfect_information():
    """The baseline a study compares degraded perception against."""
    world = make_world(("RED-01", Team.RED, (10_000.0, 0.0, 6000.0)))
    observation = observe(SensorModel(SensorConfig(enabled=False), seed=1), world)

    assert observation.confidence == 1.0
    assert observation.contacts[0].distance_m == pytest.approx(10_000.0)
    assert np.allclose(observation.position, world.get("BLUE-01").position)


# --------------------------------------------------------------- integration


def test_agents_perceive_through_the_sensor_model(settings):
    engine = SimulationEngine(settings=settings)
    engine.load_scenario("demo_alpha")
    engine.step(60 * 20)

    agent = next(a for a in engine.agents.agents if a.entity_id == "BLUE-02")
    summary = agent.last_decision.observation_summary

    assert summary["confidence"] < 1.0, "a degraded observation cannot be perfectly confident"
    assert "measured_contacts" in summary


def test_an_agent_cannot_reach_the_truth_state(settings):
    """The observation must be an independent copy, not a window into the world."""
    engine = SimulationEngine(settings=settings)
    engine.load_scenario("demo_alpha")
    engine.sensors.record(engine.world)

    observation = engine.sensors.observe("AGENT-BLUE-01", engine.world.get("BLUE-01"), engine.world)
    observation.position[0] = 999_999.0

    assert engine.world.get("BLUE-01").position[0] != 999_999.0


def test_perception_does_not_break_determinism(settings):
    def run() -> str:
        engine = SimulationEngine(settings=settings)
        engine.load_scenario("demo_alpha")
        engine.step(60 * 45)
        return engine.world.state_hash

    assert run() == run()


def test_agents_still_fly_under_degraded_perception(settings):
    engine = SimulationEngine(settings=settings)
    engine.load_scenario("demo_alpha")
    engine.step(60 * 120)

    for entity in engine.world.entities.values():
        assert entity.status is EntityStatus.ACTIVE
        assert 3_000.0 < entity.altitude < 12_000.0, f"{entity.id} left sensible altitude"

    wingman = next(a for a in engine.agents.agents if a.entity_id == "BLUE-02")
    assert wingman.last_decision.behaviour.value == "FORMATION", "should still hold formation"
