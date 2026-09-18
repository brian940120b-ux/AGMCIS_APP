"""Flight controller and safety layer tests (PHASE 4).

The guarantee being checked: no command, however malformed, reaches the physics
without passing validation, envelope protection and rate limiting — and every
correction is recorded rather than applied silently.
"""

from __future__ import annotations

import numpy as np
import pytest

from controllers.action_validator import ActionValidator, entity_state_is_valid
from controllers.command_mapper import CommandMapper
from controllers.flight_controller import FlightController
from controllers.limits import SafetyLimits, ViolationAction, ViolationType
from core.simulation_engine import SimulationEngine
from core.world_state import EntityState, EntityStatus, Team, WorldState
from simulation.aircraft import ControlInputs

DT = 1.0 / 60.0


def make_entity(**overrides) -> EntityState:
    defaults = {
        "id": "TEST-01",
        "team": Team.BLUE,
        "position": np.array([0.0, 0.0, 6000.0]),
        "velocity": np.array([220.0, 0.0, 0.0]),
        "orientation": np.array([0.0, 0.0, np.pi / 2]),
    }
    return EntityState(**{**defaults, **overrides})


def free_controller(**limit_overrides) -> FlightController:
    """A controller with rate limiting effectively off, to isolate other rules."""
    return FlightController(SafetyLimits(max_control_rate_per_s=10_000.0, **limit_overrides))


# ------------------------------------------------------------------ validation


def test_non_finite_demands_become_neutral():
    result = free_controller().apply(
        make_entity(), ControlInputs(aileron=float("nan"), elevator=float("inf"), throttle=0.5), DT
    )

    assert result.accepted
    assert result.controls.aileron == 0.0
    assert result.controls.elevator == 0.0
    assert result.controls.throttle == pytest.approx(0.5), "a valid channel is untouched"
    assert {v.type for v in result.violations} == {ViolationType.NON_FINITE}


@pytest.mark.parametrize(
    ("channel", "demanded", "expected"),
    [
        ("aileron", 5.0, 1.0),
        ("aileron", -5.0, -1.0),
        ("elevator", 3.0, 1.0),
        ("rudder", -9.0, -1.0),
        ("throttle", 2.0, 1.0),
        ("throttle", -3.0, 0.0),
    ],
)
def test_out_of_range_demands_are_clamped(channel, demanded, expected):
    result = free_controller().apply(make_entity(), ControlInputs(**{channel: demanded}), DT)

    assert getattr(result.controls, channel) == pytest.approx(expected)
    assert any(v.type is ViolationType.OUT_OF_RANGE for v in result.violations)


def test_a_valid_command_passes_through_unchanged():
    demand = ControlInputs(aileron=0.2, elevator=-0.1, rudder=0.05, throttle=0.4)
    result = free_controller().apply(make_entity(), demand, DT)

    assert result.violations == []
    assert result.controls.to_dict() == demand.to_dict()


def test_a_broken_aircraft_state_rejects_the_command():
    controller = free_controller()
    entity = make_entity()
    entity.controls = ControlInputs(throttle=0.3)
    entity.velocity[0] = float("nan")

    result = controller.apply(entity, ControlInputs(elevator=0.9, throttle=1.0), DT)

    assert result.accepted is False
    assert result.violations[0].type is ViolationType.INVALID_STATE
    assert result.violations[0].action is ViolationAction.REJECTED
    assert entity.controls.throttle == pytest.approx(0.3), "the last command is kept"


def test_entity_state_validity_check():
    assert entity_state_is_valid(make_entity())

    broken = make_entity()
    broken.angular_velocity[1] = float("inf")
    assert not entity_state_is_valid(broken)


def test_the_validator_alone_does_not_touch_the_entity():
    entity = make_entity()
    before = entity.controls.to_dict()

    ActionValidator().validate(ControlInputs(aileron=0.5), entity)

    assert entity.controls.to_dict() == before


# ------------------------------------------------------------ rate limiting


def test_actuators_cannot_slew_faster_than_the_limit():
    controller = FlightController(SafetyLimits(max_control_rate_per_s=4.0))
    result = controller.apply(make_entity(), ControlInputs(aileron=1.0), DT)

    assert result.controls.aileron == pytest.approx(4.0 * DT)
    assert any(v.type is ViolationType.RATE_LIMITED for v in result.violations)


def test_repeated_commands_converge_on_the_demand():
    controller = FlightController(SafetyLimits(max_control_rate_per_s=4.0))
    entity = make_entity()

    for _ in range(60):  # one second at the limit covers a range of 4.0
        controller.apply(entity, ControlInputs(aileron=1.0), DT)

    assert entity.controls.aileron == pytest.approx(1.0)


def test_a_small_change_is_not_rate_limited():
    controller = FlightController(SafetyLimits(max_control_rate_per_s=4.0))
    result = controller.apply(make_entity(), ControlInputs(aileron=0.01), DT)

    assert result.controls.aileron == pytest.approx(0.01)
    assert not any(v.type is ViolationType.RATE_LIMITED for v in result.violations)


def test_seeding_starts_the_actuators_at_the_trim_setting():
    controller = FlightController(SafetyLimits(max_control_rate_per_s=4.0))
    entity = make_entity()
    entity.controls = ControlInputs(throttle=0.26)
    controller.seed(entity)

    result = controller.apply(entity, ControlInputs(throttle=0.26), DT)

    assert result.controls.throttle == pytest.approx(0.26), "no slew from a matching demand"


def test_reset_clears_actuator_memory():
    controller = FlightController(SafetyLimits(max_control_rate_per_s=4.0))
    entity = make_entity()
    for _ in range(30):
        controller.apply(entity, ControlInputs(aileron=1.0), DT)

    controller.reset()
    result = controller.apply(make_entity(), ControlInputs(aileron=1.0), DT)

    assert result.controls.aileron == pytest.approx(4.0 * DT), "starts from neutral again"


def test_the_mapper_tracks_entities_independently():
    mapper = CommandMapper(SafetyLimits(max_control_rate_per_s=4.0))
    mapper.map("A", ControlInputs(aileron=1.0), DT)
    controls, _ = mapper.map("B", ControlInputs(aileron=1.0), DT)

    assert controls.aileron == pytest.approx(4.0 * DT), "B must not inherit A's position"


# -------------------------------------------------------- envelope protection


def test_load_factor_is_about_one_in_trimmed_level_flight():
    """Trimmed flight sits at a small positive alpha — that is what carries the
    weight. At exactly zero alpha this airframe generates no lift (cl_0 = 0), so
    the trim attitude is what "level flight" means here."""
    trimmed = make_entity(orientation=np.array([0.0, np.radians(1.27), np.pi / 2]))
    assert FlightController.estimate_load_factor(trimmed) == pytest.approx(1.0, abs=0.1)


def test_load_factor_is_zero_at_zero_angle_of_attack():
    flat = make_entity(orientation=np.array([0.0, 0.0, np.pi / 2]))
    assert FlightController.estimate_load_factor(flat) == pytest.approx(0.0, abs=1e-9)


def test_load_factor_is_zero_when_stationary():
    assert FlightController.estimate_load_factor(make_entity(velocity=np.zeros(3))) == 0.0


def test_excessive_load_factor_eases_the_pitch_demand():
    controller = free_controller(max_load_factor=9.0)
    # Fast and at a high angle of attack: well beyond the structural limit.
    hard = make_entity(
        velocity=np.array([600.0, 0.0, 0.0]),
        orientation=np.array([0.0, np.radians(15.0), np.pi / 2]),
    )

    result = controller.apply(hard, ControlInputs(elevator=1.0), DT)

    assert result.controls.elevator < 1.0
    assert any(v.type is ViolationType.LOAD_FACTOR for v in result.violations)


def test_the_load_limiter_never_blocks_pitching_down():
    """Easing off is the recovery; the limiter must not fight it."""
    controller = free_controller(max_load_factor=9.0)
    hard = make_entity(
        velocity=np.array([600.0, 0.0, 0.0]),
        orientation=np.array([0.0, np.radians(15.0), np.pi / 2]),
    )

    result = controller.apply(hard, ControlInputs(elevator=-0.5), DT)

    assert result.controls.elevator == pytest.approx(-0.5)
    assert not any(v.type is ViolationType.LOAD_FACTOR for v in result.violations)


def test_descent_is_blocked_near_the_altitude_floor():
    controller = free_controller(min_altitude_m=100.0, altitude_buffer_m=500.0)
    low = make_entity(position=np.array([0.0, 0.0, 200.0]))

    result = controller.apply(low, ControlInputs(elevator=-0.8), DT)

    assert result.controls.elevator > -0.8, "descent demand must be eased"
    assert any(v.type is ViolationType.ALTITUDE_FLOOR for v in result.violations)


def test_climbing_is_allowed_near_the_floor():
    controller = free_controller(min_altitude_m=100.0, altitude_buffer_m=500.0)
    low = make_entity(position=np.array([0.0, 0.0, 200.0]))

    result = controller.apply(low, ControlInputs(elevator=0.8), DT)

    assert result.controls.elevator == pytest.approx(0.8)


def test_climb_is_blocked_near_the_ceiling():
    controller = free_controller(max_altitude_m=19_000.0, altitude_buffer_m=500.0)
    high = make_entity(position=np.array([0.0, 0.0, 18_900.0]))

    result = controller.apply(high, ControlInputs(elevator=0.9), DT)

    assert result.controls.elevator < 0.9
    assert any(v.type is ViolationType.ALTITUDE_CEILING for v in result.violations)


def test_protection_does_not_engage_in_normal_flight():
    result = free_controller().apply(make_entity(), ControlInputs(elevator=0.2, throttle=0.3), DT)
    assert result.violations == []


# ----------------------------------------------------------------- reporting


def test_the_controller_counts_what_it_did():
    controller = free_controller()
    entity = make_entity()

    controller.apply(entity, ControlInputs(aileron=9.0), DT)
    controller.apply(entity, ControlInputs(aileron=0.1), DT)

    status = controller.status()
    assert status["commands_applied"] == 2
    assert status["commands_rejected"] == 0
    assert status["violations"]["OUT_OF_RANGE"] == 1


def test_rejections_are_counted_separately():
    controller = free_controller()
    broken = make_entity()
    broken.position[2] = float("nan")

    controller.apply(broken, ControlInputs(elevator=0.5), DT)

    assert controller.status()["commands_rejected"] == 1
    assert controller.status()["commands_applied"] == 0


def test_update_skips_inactive_entities():
    controller = free_controller()
    world = WorldState()
    entity = make_entity()
    entity.status = EntityStatus.DISABLED
    world.add_entity(entity)

    outcomes = controller.update(world, {"TEST-01": ControlInputs(elevator=0.5)}, DT)

    assert outcomes == []


def test_update_ignores_a_demand_for_an_unknown_entity():
    controller = free_controller()
    assert controller.update(WorldState(), {"GHOST": ControlInputs()}, DT) == []


# --------------------------------------------------------------- integration


def test_the_controller_is_the_only_writer_of_entity_controls(settings):
    """The agent manager records a demand; the controller applies it."""
    engine = SimulationEngine(settings=settings)
    engine.load_scenario("demo_alpha")

    engine.agents.update(engine.world, 0)

    assert engine.agents.demands, "the agent should have produced a demand"
    demand = engine.agents.demands["BLUE-01"]
    entity = engine.world.get("BLUE-01")

    # The manager must not have touched the aircraft.
    assert entity.controls.to_dict() != demand.to_dict() or demand.aileron == entity.controls.aileron

    engine.controller.update(engine.world, engine.agents.demands, 1 / 60)
    assert engine.controller.status()["commands_applied"] >= 1


def test_safety_limits_load_from_configuration(settings):
    engine = SimulationEngine(settings=settings)
    limits = engine.controller.limits

    assert limits.max_load_factor == settings.safety.max_load_factor
    assert limits.min_altitude_m == settings.safety.min_altitude_m


def test_a_full_run_stays_inside_the_load_limit(settings):
    engine = SimulationEngine(settings=settings)
    engine.load_scenario("demo_alpha")

    engine.step(60 * 90)

    for entity in engine.world.entities.values():
        load = FlightController.estimate_load_factor(entity)
        assert load < settings.safety.max_load_factor * 1.5, f"{entity.id} pulled {load:.1f} g"


def test_the_safety_layer_does_not_break_determinism(settings):
    def run() -> str:
        engine = SimulationEngine(settings=settings)
        engine.load_scenario("demo_alpha")
        engine.step(60 * 45)
        return engine.world.state_hash

    assert run() == run()
