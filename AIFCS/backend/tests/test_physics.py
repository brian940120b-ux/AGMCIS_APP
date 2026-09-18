"""6DOF flight physics tests (PHASE 2).

These check physical behaviour against closed-form expectations and known
aerodynamic conventions, not just that the code runs.
"""

from __future__ import annotations

import numpy as np
import pytest

from core.world_state import EntityState, EntityStatus, WorldState
from simulation.aircraft import ControlInputs, get_aircraft
from simulation.physics import (
    MIN_AIRSPEED_MPS,
    Simple6DOFModel,
    aerodynamic_angles,
    enu_to_ned,
    euler_from_quat,
    ned_to_enu,
    quat_from_euler,
    quat_normalize,
    rotation_body_to_ned,
)

DT = 1.0 / 60.0
GRAVITY = 9.80665


def make_entity(**overrides) -> EntityState:
    """A unit trimmed for level flight heading east, unless overridden."""
    defaults = {
        "id": "TEST-01",
        "position": [0.0, 0.0, 8000.0],
        "velocity": [220.0, 0.0, 0.0],
        "orientation": [0.0, 0.0, np.pi / 2],  # yaw 90 deg = east, matching velocity
        "controls": ControlInputs(throttle=0.26),
    }
    return EntityState(**{**defaults, **overrides})


def fly(entity: EntityState, ticks: int, world: WorldState | None = None) -> EntityState:
    world = world or WorldState()
    if entity.id not in world.entities:
        world.add_entity(entity)
    model = Simple6DOFModel()
    for _ in range(ticks):
        model.integrate(world, DT)
    return entity


def body_angles(entity: EntityState) -> tuple[float, float, float]:
    rotation = rotation_body_to_ned(entity.attitude_quaternion)
    return aerodynamic_angles(rotation.T @ enu_to_ned(entity.velocity))


# ------------------------------------------------------------- frames & math


def test_enu_ned_conversion_is_its_own_inverse():
    vector = np.array([100.0, 200.0, 300.0])  # east, north, up
    assert ned_to_enu(enu_to_ned(vector)) == pytest.approx(vector)


def test_enu_to_ned_maps_axes_correctly():
    # 100 m east, 200 m north, 300 m up -> 200 north, 100 east, -300 down
    assert enu_to_ned(np.array([100.0, 200.0, 300.0])) == pytest.approx([200.0, 100.0, -300.0])


@pytest.mark.parametrize(
    "euler",
    [(0.0, 0.0, 0.0), (0.3, -0.2, 1.1), (-0.9, 0.4, -2.5), (0.0, 1.2, 0.0)],
)
def test_quaternion_euler_round_trip(euler):
    assert euler_from_quat(quat_from_euler(*euler)) == pytest.approx(euler, abs=1e-9)


def test_rotation_matrix_is_orthonormal():
    rotation = rotation_body_to_ned(quat_from_euler(0.4, -0.3, 2.0))
    assert rotation @ rotation.T == pytest.approx(np.eye(3), abs=1e-12)
    assert np.linalg.det(rotation) == pytest.approx(1.0)


def test_quat_normalize_survives_a_zero_quaternion():
    assert quat_normalize(np.zeros(4)) == pytest.approx([1.0, 0.0, 0.0, 0.0])


def test_level_flight_has_zero_angle_of_attack_definitionally():
    # Body velocity purely forward -> alpha and beta are zero.
    airspeed, alpha, beta = aerodynamic_angles(np.array([220.0, 0.0, 0.0]))
    assert airspeed == pytest.approx(220.0)
    assert alpha == 0.0
    assert beta == 0.0


def test_relative_wind_below_the_nose_is_positive_alpha():
    _, alpha, _ = aerodynamic_angles(np.array([200.0, 0.0, 20.0]))  # w positive = down in FRD
    assert alpha > 0


def test_aerodynamic_angles_are_suppressed_below_the_minimum_airspeed():
    _, alpha, beta = aerodynamic_angles(np.array([MIN_AIRSPEED_MPS / 2, 0.3, 0.3]))
    assert (alpha, beta) == (0.0, 0.0)


# ------------------------------------------------------------------- gravity


def test_free_fall_matches_the_closed_form():
    """With no thrust and negligible airspeed, drop ≈ ½gt² for the first second."""
    entity = make_entity(velocity=[0.0, 0.0, 0.0], orientation=[0.0, 0.0, 0.0], controls=ControlInputs())
    start = entity.altitude
    fly(entity, 60)

    # Drag is not zero once it is moving, so allow a small shortfall.
    assert entity.altitude == pytest.approx(start - 0.5 * GRAVITY, abs=0.05)
    assert entity.velocity[2] == pytest.approx(-GRAVITY, abs=0.05)


def test_drag_gives_a_dive_a_finite_terminal_speed():
    entity = make_entity(
        position=[0.0, 0.0, 30000.0],
        velocity=[0.0, 0.0, -50.0],
        orientation=[0.0, -np.pi / 2, 0.0],  # nose straight down
        controls=ControlInputs(),
    )
    fly(entity, 60 * 30)
    speed = float(np.linalg.norm(entity.velocity))

    assert np.isfinite(speed)
    assert speed < 400.0, "drag must bound the dive speed"


# ------------------------------------------------------------------ trimmed


def test_trimmed_cruise_holds_altitude():
    """Hands-off at the trim speed: altitude drifts only slightly over a minute."""
    entity = make_entity()
    start = entity.altitude
    fly(entity, 60 * 60)

    assert abs(entity.altitude - start) < 150.0, "trimmed flight should stay near its altitude"
    assert entity.speed == pytest.approx(220.0, abs=15.0)


def test_static_stability_drives_alpha_to_trim():
    entity = make_entity()
    fly(entity, 60 * 20)

    params = get_aircraft(None)
    expected_trim = params.cm_0 / -params.cm_alpha
    _, alpha, _ = body_angles(entity)

    assert alpha == pytest.approx(expected_trim, abs=0.01)


def test_heading_is_preserved_without_lateral_input():
    entity = make_entity()
    fly(entity, 60 * 30)
    assert entity.heading_deg == pytest.approx(90.0, abs=1.0)


# ------------------------------------------------------------------ controls


def test_positive_elevator_pitches_the_nose_up():
    entity = make_entity(controls=ControlInputs(elevator=0.3, throttle=0.3))
    fly(entity, 90)
    assert entity.orientation[1] > 0.05, "elevator > 0 must mean nose up"


def test_negative_elevator_pitches_the_nose_down():
    entity = make_entity(controls=ControlInputs(elevator=-0.3, throttle=0.3))
    fly(entity, 90)
    assert entity.orientation[1] < -0.05


def test_pulling_up_gains_altitude():
    entity = make_entity(controls=ControlInputs(elevator=0.25, throttle=0.8))
    start = entity.altitude
    fly(entity, 180)
    assert entity.altitude > start + 50.0


def test_positive_aileron_rolls_right():
    entity = make_entity(controls=ControlInputs(aileron=0.4, throttle=0.3))
    fly(entity, 60)
    assert entity.orientation[0] > 0.1, "aileron > 0 must mean right wing down"


def test_negative_aileron_rolls_left():
    entity = make_entity(controls=ControlInputs(aileron=-0.4, throttle=0.3))
    fly(entity, 60)
    assert entity.orientation[0] < -0.1


def test_roll_rate_is_bounded_by_damping():
    """Roll damping must stop full aileron producing an unbounded rate."""
    entity = make_entity(controls=ControlInputs(aileron=1.0, throttle=0.3))
    fly(entity, 60 * 3)
    roll_rate_deg = abs(np.degrees(entity.angular_velocity[0]))
    assert roll_rate_deg < 300.0, f"roll rate {roll_rate_deg:.0f} deg/s is unflyable"


def test_angle_of_attack_is_bounded_by_elevator_authority():
    entity = make_entity(controls=ControlInputs(elevator=1.0, throttle=0.5))
    fly(entity, 120)
    _, alpha, _ = body_angles(entity)
    assert abs(np.degrees(alpha)) < 30.0, "full elevator must not command an absurd alpha"


def test_throttle_accelerates_and_idle_decelerates():
    fast = fly(make_entity(controls=ControlInputs(throttle=1.0)), 60 * 5)
    slow = fly(make_entity(id="TEST-02", controls=ControlInputs(throttle=0.0)), 60 * 5)
    assert fast.speed > 220.0
    assert slow.speed < 220.0


# ------------------------------------------------------------------ robustness


def test_non_finite_controls_cannot_poison_the_state():
    entity = make_entity()
    entity.controls = ControlInputs(elevator=float("nan"), throttle=float("inf"))
    fly(entity, 60)

    assert np.all(np.isfinite(entity.position))
    assert np.all(np.isfinite(entity.velocity))
    assert entity.status is EntityStatus.ACTIVE


def test_quaternion_stays_normalised_over_a_long_manoeuvre():
    entity = make_entity(controls=ControlInputs(aileron=0.5, elevator=0.3, throttle=0.9))
    fly(entity, 60 * 60)
    assert float(np.linalg.norm(entity.attitude_quaternion)) == pytest.approx(1.0, abs=1e-9)


def test_inactive_entities_are_not_integrated():
    entity = make_entity()
    entity.status = EntityStatus.DISABLED
    before = entity.position.copy()
    fly(entity, 120)
    assert entity.position.tolist() == before.tolist()


def test_vertical_flight_does_not_hit_gimbal_lock():
    """Euler angles would break at 90 degrees; the quaternion must not."""
    entity = make_entity(
        orientation=[0.0, np.pi / 2 - 0.01, 0.0],  # nose almost straight up
        velocity=[0.0, 0.0, 200.0],
        controls=ControlInputs(elevator=0.5, throttle=1.0),
    )
    fly(entity, 60 * 5)

    assert np.all(np.isfinite(entity.position))
    assert np.all(np.isfinite(entity.orientation))
    assert float(np.linalg.norm(entity.attitude_quaternion)) == pytest.approx(1.0, abs=1e-9)


def test_physics_is_deterministic():
    a = fly(make_entity(controls=ControlInputs(aileron=0.2, elevator=0.1, throttle=0.7)), 600)
    b = fly(make_entity(controls=ControlInputs(aileron=0.2, elevator=0.1, throttle=0.7)), 600)
    assert a.position.tolist() == b.position.tolist()
    assert a.attitude_quaternion.tolist() == b.attitude_quaternion.tolist()
