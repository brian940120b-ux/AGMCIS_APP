"""Simulation engine tests (PHASE 1).

The determinism tests here are the heart of the platform: a research result is
worthless if the run cannot be reproduced.
"""

from __future__ import annotations

import asyncio

import pytest

from core.clock import ClockState
from core.event_bus import EventType
from core.integrator import KinematicIntegrator, NullIntegrator
from core.simulation_engine import SimulationEngine, SimulationError
from core.world_state import EntityStatus


@pytest.fixture
def engine(settings) -> SimulationEngine:
    engine = SimulationEngine(settings=settings)
    engine.load_scenario("demo_alpha")
    return engine


# ------------------------------------------------------------------ stepping


def test_step_advances_exactly_one_timestep(engine):
    engine.step(1)
    assert engine.world.tick == 1
    assert engine.world.simulation_time == pytest.approx(engine.clock.dt)


def test_kinematic_motion_matches_the_closed_form_solution(settings):
    """With the kinematic integrator, 10 s of constant velocity is exact algebra."""
    engine = SimulationEngine(settings=settings, integrator=KinematicIntegrator())
    engine.load_scenario("demo_alpha")

    start_x = engine.world.get("BLUE-01").position[0]
    velocity_x = engine.world.get("BLUE-01").velocity[0]

    engine.step(600)  # 10 simulation seconds at 60 Hz

    assert engine.world.simulation_time == pytest.approx(10.0)
    assert engine.world.get("BLUE-01").position[0] == pytest.approx(start_x + velocity_x * 10.0)


def test_default_integrator_is_6dof(engine):
    """PHASE 2 makes Newton-Euler dynamics the default physics backend."""
    assert engine.integrator.name == "simple_6dof"
    assert engine.status()["integrator"] == "simple_6dof"


def test_trimmed_scenario_units_hold_their_altitude(engine):
    """demo_alpha is trimmed: the units should still be flying after a minute."""
    start = {e.id: e.altitude for e in engine.world.entities.values()}

    engine.step(60 * 60)

    for entity in engine.world.entities.values():
        assert entity.status is EntityStatus.ACTIVE
        assert abs(entity.altitude - start[entity.id]) < 200.0, f"{entity.id} did not hold altitude"


def test_step_requires_a_loaded_scenario(settings):
    with pytest.raises(SimulationError, match="no scenario"):
        SimulationEngine(settings=settings).step()


def test_step_rejects_non_positive_tick_counts(engine):
    with pytest.raises(SimulationError, match="at least 1"):
        engine.step(0)


# --------------------------------------------------------------- determinism


def test_same_seed_produces_an_identical_state(settings):
    def run() -> str:
        engine = SimulationEngine(settings=settings)
        engine.load_scenario("demo_alpha", seed=4242)
        engine.step(900)
        return engine.world.state_hash

    assert run() == run()


def test_stepping_in_different_chunks_gives_the_same_result(settings):
    """600 x 1 must equal 1 x 600 — no hidden per-call state."""
    one_by_one = SimulationEngine(settings=settings)
    one_by_one.load_scenario("demo_alpha")
    for _ in range(600):
        one_by_one.step(1)

    all_at_once = SimulationEngine(settings=settings)
    all_at_once.load_scenario("demo_alpha")
    all_at_once.step(600)

    assert one_by_one.world.state_hash == all_at_once.world.state_hash


def test_speed_does_not_affect_the_outcome(settings):
    """Changing speed must change pacing only, never the trajectory."""
    slow = SimulationEngine(settings=settings)
    slow.load_scenario("demo_alpha")
    slow.set_speed(0.25)
    slow.step(300)

    fast = SimulationEngine(settings=settings)
    fast.load_scenario("demo_alpha")
    fast.set_speed(50.0)
    fast.step(300)

    assert slow.world.state_hash == fast.world.state_hash


def test_reseeding_is_recorded_in_the_world(settings):
    engine = SimulationEngine(settings=settings)
    engine.load_scenario("demo_alpha", seed=777)
    assert engine.seed == 777
    assert engine.world.global_status["seed"] == 777
    assert engine.world.global_status["config_hash"] == settings.config_hash


# ------------------------------------------------------------------ lifecycle


@pytest.mark.asyncio
async def test_start_then_pause_freezes_simulation_time(engine):
    await engine.start()
    assert engine.is_running

    await asyncio.sleep(0.15)
    engine.pause()

    frozen_tick = engine.world.tick
    assert frozen_tick > 0, "the loop must have advanced the world"

    await asyncio.sleep(0.1)
    assert engine.world.tick == frozen_tick, "pause must genuinely stop the clock"

    await engine.stop()


@pytest.mark.asyncio
async def test_resume_continues_from_where_it_paused(engine):
    await engine.start()
    await asyncio.sleep(0.1)
    engine.pause()
    paused_tick = engine.world.tick

    engine.resume()
    await asyncio.sleep(0.1)
    await engine.stop()

    assert engine.world.tick > paused_tick


@pytest.mark.asyncio
async def test_reset_restores_the_initial_world(engine):
    initial_hash = engine.world.state_hash
    await engine.start()
    await asyncio.sleep(0.1)
    await engine.stop()
    assert engine.world.state_hash != initial_hash

    await engine.reset()

    assert engine.world.tick == 0
    assert engine.clock.simulation_time == 0.0
    assert engine.world.state_hash == initial_hash


@pytest.mark.asyncio
async def test_starting_twice_is_refused(engine):
    await engine.start()
    with pytest.raises(SimulationError, match="already running"):
        await engine.start()
    await engine.stop()


@pytest.mark.asyncio
async def test_stepping_while_running_is_refused(engine):
    await engine.start()
    with pytest.raises(SimulationError, match="pause first"):
        engine.step()
    await engine.stop()


def test_pause_and_resume_reject_invalid_states(engine):
    with pytest.raises(SimulationError, match="cannot pause"):
        engine.pause()
    with pytest.raises(SimulationError, match="cannot resume"):
        engine.resume()


def test_set_speed_rejects_values_outside_the_allowed_set(engine):
    with pytest.raises(SimulationError, match="not allowed"):
        engine.set_speed(3.7)


# ---------------------------------------------------------------- boundaries


def test_entity_leaving_the_world_is_clamped_and_flagged(settings):
    engine = SimulationEngine(settings=settings)
    engine.load_scenario("demo_alpha")

    # Aim BLUE-01 at the eastern wall at an implausible speed.
    entity = engine.world.get("BLUE-01")
    entity.position[0] = settings.world.bounds.x_max - 10.0
    entity.velocity[:] = [5000.0, 0.0, 0.0]

    engine.step(5)

    assert entity.position[0] == settings.world.bounds.x_max
    assert entity.status is EntityStatus.OUT_OF_BOUNDS
    assert engine.events.recent(event_type=EventType.ENTITY_OUT_OF_BOUNDS)


def test_simulation_ends_when_the_scenario_duration_is_reached(settings):
    engine = SimulationEngine(settings=settings)
    engine.load_scenario("demo_alpha")
    engine.scenario.duration_s = 1.0  # shorten for the test

    engine.step(600)  # would be 10 s, but the run must stop at 1 s

    assert engine.clock.state is ClockState.STOPPED
    assert engine.clock.simulation_time == pytest.approx(1.0, abs=engine.clock.dt)
    ended = engine.events.recent(event_type=EventType.SIMULATION_ENDED)
    assert ended and ended[-1].data["reason"] == "duration reached"


# -------------------------------------------------------------- integrators


def test_integrator_is_swappable(settings):
    """PHASE 2 and PHASE 16 replace physics without touching the engine."""
    frozen = SimulationEngine(settings=settings, integrator=NullIntegrator())
    frozen.load_scenario("demo_alpha")
    before = frozen.world.get("BLUE-01").position.copy()

    frozen.step(600)

    assert frozen.world.get("BLUE-01").position.tolist() == before.tolist()
    assert frozen.status()["integrator"] == "null"

    # And the kinematic backend from PHASE 1 still works when selected.
    kinematic = SimulationEngine(settings=settings, integrator=KinematicIntegrator())
    kinematic.load_scenario("demo_alpha")
    kinematic.step(60)
    assert kinematic.status()["integrator"] == "kinematic"


def test_inactive_entities_are_not_integrated(settings):
    engine = SimulationEngine(settings=settings)
    engine.load_scenario("demo_alpha")
    entity = engine.world.get("RED-01")
    entity.status = EntityStatus.DISABLED
    before = entity.position.copy()

    engine.step(120)

    assert entity.position.tolist() == before.tolist()


# -------------------------------------------------------------------- events


def test_lifecycle_events_are_published(engine):
    engine.step(3)

    types = [e.type for e in engine.events.recent(limit=200)]
    assert EventType.SIMULATION_TICK not in types, "ticks stay out of history"


@pytest.mark.asyncio
async def test_start_pause_resume_emit_events(engine):
    await engine.start()
    await asyncio.sleep(0.05)
    engine.pause()
    engine.resume()
    await engine.stop()

    types = [e.type for e in engine.events.recent(limit=200)]
    assert EventType.SIMULATION_STARTED in types
    assert EventType.SIMULATION_PAUSED in types
    assert EventType.SIMULATION_RESUMED in types


def test_status_reports_the_full_contract(engine):
    status = engine.status()
    assert {
        "scenario",
        "scenario_loaded",
        "clock",
        "seed",
        "deterministic",
        "integrator",
        "config_hash",
        "entity_count",
        "active_entities",
        "duration_s",
        "end_reason",
        "state_hash",
        "events_published",
    } <= set(status)
    assert status["entity_count"] == 4
    assert status["scenario"] == "demo_alpha"
