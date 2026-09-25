"""Differential tests against the organiser's own implementation (COMP PHASE 1).

"Follow the organiser" is only worth saying if it can be checked, so the
reference functions are vendored below, verbatim apart from having their module
globals passed in, and used as an oracle: random telemetry goes into both, and
the two 20-vectors have to agree.

That gives the rewrite something the reference package does not have — a single
implementation used by training and by the competition client — without letting
the rewrite quietly become a different environment from the one the host runs
and the reference policy was trained in.

Source: NCSIST-AIPilot-03 `player1_Loadmodel.py` and NCSIST-AIPilot-05
`envs/jsbsimEnv/jsbsimEnv.py`, competition package dated 2026/09/03.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from competition.action import (
    ELEVATOR_LIMIT_ABOVE_MACH,
    ELEVATOR_LIMIT_HIGH_SPEED,
    JoystickState,
    shape_command,
)
from competition.protocol import (
    CMD_PACKET_BYTES,
    OBS_PACKET_BYTES,
    PLAYER_CMD_TRAILER,
    Command,
    PlayerState,
    ProtocolError,
    decode_command,
    decode_observation,
    encode_command,
)
from competition.state import StateEncoder, Telemetry

# --------------------------------------------------------------- the oracle


def reference_get_state(obs: np.ndarray, prev_distance_m: float | None) -> tuple[np.ndarray, float]:
    """`get_state` from player1_Loadmodel.py, with its global made a parameter."""
    earth_radius_m = 6378137.0

    lat1_deg, lon1_deg = obs[0], obs[1]
    alt1_m = obs[2] * 0.3048
    lat2_deg, lon2_deg = obs[20], obs[21]
    alt2_m = obs[22] * 0.3048

    lat1_rad, lat2_rad = math.radians(lat1_deg), math.radians(lat2_deg)
    lon1_rad, lon2_rad = math.radians(lon1_deg), math.radians(lon2_deg)

    dn_m = (lat2_rad - lat1_rad) * earth_radius_m
    de_m = (lon2_rad - lon1_rad) * earth_radius_m * math.cos(lat1_rad)
    dd_m = -(alt2_m - alt1_m)
    pos_rel_ned_m = np.array([dn_m, de_m, dd_m], dtype=np.float64)

    enemy_vel_ned_mps = np.array([obs[23], obs[24], obs[25]], dtype=np.float64) * 0.3048
    distance_m = float(np.linalg.norm(pos_rel_ned_m))

    if prev_distance_m is None:
        prev_distance_m = distance_m
    vc_mps = (prev_distance_m - distance_m) / (1 / 60)

    v_los_m = np.zeros(3) - pos_rel_ned_m
    norm_v2 = np.linalg.norm(enemy_vel_ned_mps)
    norm_los = np.linalg.norm(v_los_m)
    if norm_v2 < 1e-5 or norm_los < 1e-5:
        enemy_3d_angle_deg = 180.0
    else:
        cos_theta = np.clip(np.dot(enemy_vel_ned_mps, v_los_m) / (norm_v2 * norm_los), -1.0, 1.0)
        enemy_3d_angle_deg = float(np.degrees(np.arccos(cos_theta)))

    phi_rad = math.radians(obs[3])
    theta_rad = math.radians(obs[4])
    psi_rad = math.radians(obs[5])

    rx = np.array([[1, 0, 0], [0, np.cos(phi_rad), np.sin(phi_rad)], [0, -np.sin(phi_rad), np.cos(phi_rad)]])
    ry = np.array(
        [
            [np.cos(theta_rad), 0, -np.sin(theta_rad)],
            [0, 1, 0],
            [np.sin(theta_rad), 0, np.cos(theta_rad)],
        ]
    )
    rz = np.array([[np.cos(psi_rad), np.sin(psi_rad), 0], [-np.sin(psi_rad), np.cos(psi_rad), 0], [0, 0, 1]])
    v_body_m = (rx @ ry @ rz) @ pos_rel_ned_m
    xb_m, yb_m, zb_m = v_body_m

    own_azimuth_deg = np.degrees(np.arctan2(yb_m, xb_m))
    own_elevation_deg = np.degrees(np.arctan2(-zb_m, np.sqrt(xb_m**2 + yb_m**2)))

    state = np.zeros(20, dtype=np.float32)
    state[0] = distance_m / 5000.0
    state[1] = (alt2_m - alt1_m) / 5000.0
    state[2] = own_elevation_deg / 90.0
    state[3] = own_azimuth_deg / 180.0
    state[4] = enemy_3d_angle_deg / 180.0
    state[5] = np.sin(phi_rad)
    state[6] = np.cos(phi_rad)
    state[7] = np.sin(theta_rad)
    state[8] = np.cos(theta_rad)
    state[9] = obs[18] / 30.0
    state[10] = obs[19] / 30.0
    state[11] = alt1_m / 5000.0
    state[12] = obs[13] * 0.3048 / 340.0
    state[13] = vc_mps / 340.0
    state[14] = obs[15] * 0.3048 / 340.0
    state[15] = obs[16] * 0.3048 / 340.0
    state[16] = obs[17] * 0.3048 / 340.0
    state[17] = obs[9]
    state[18] = obs[10]
    state[19] = obs[11]
    return state, distance_m


def reference_process_joystick_action(
    rl_action: np.ndarray, obs: np.ndarray, last_actual_action: np.ndarray
) -> np.ndarray:
    """`process_joystick_action` from player1_Loadmodel.py, global parameterised."""
    processed_action = np.zeros(4)
    deadbands = np.array([0.01, 0.03, 0.06])
    exponents = np.array([1.0, 3.0, 3.0])
    max_change_per_step = np.array([0.050, 0.026, 0.013])

    current_mach = obs[13] * 0.3048 / 340
    elevator_limit = 0.4 if current_mach > 0.8 else 1.0
    action_limits = np.array([1.0, elevator_limit, 0.2])

    for i in range(3):
        act = rl_action[i]
        if abs(act) < deadbands[i]:
            act = 0.0
        act = np.sign(act) * (abs(act) ** exponents[i])
        act = np.clip(act, -action_limits[i], action_limits[i])
        last_act = last_actual_action[i]
        chage = np.clip(act - last_act, -max_change_per_step[i], max_change_per_step[i])
        processed_action[i] = last_act + chage

    target_throttle = np.clip(rl_action[3], 0.0, 1.0)
    throttle_rate_limit = 0.004
    last_throttle = last_actual_action[3]
    throttle_change = np.clip(target_throttle - last_throttle, -throttle_rate_limit, throttle_rate_limit)
    processed_action[3] = last_throttle + throttle_change
    return processed_action


# ------------------------------------------------------------- generators


def random_observation(rng: np.random.Generator) -> np.ndarray:
    """Telemetry drawn over the ranges the competition actually produces.

    Positions near the reference environment's start point, altitudes and speeds
    from the published round setup, attitudes anywhere.
    """
    lat = 23.060552 + rng.normal(0.0, 0.02)
    lon = 121.948555 + rng.normal(0.0, 0.02)
    obs = np.zeros(26, dtype=np.float64)
    obs[0] = lat
    obs[1] = lon
    obs[2] = rng.uniform(10_000, 20_000)
    obs[3] = rng.uniform(-180, 180)
    obs[4] = rng.uniform(-89, 89)
    obs[5] = rng.uniform(-180, 180)
    obs[6:9] = rng.uniform(-800, 800, 3)
    obs[9:12] = rng.uniform(-3, 3, 3)
    obs[12] = rng.uniform(200, 1200)
    obs[13] = rng.uniform(200, 1200)
    obs[14] = rng.uniform(-3, 10)
    obs[15:18] = rng.uniform(-800, 800, 3)
    obs[18] = rng.uniform(-20, 30)
    obs[19] = rng.uniform(-20, 20)
    obs[20] = lat + rng.normal(0.0, 0.01)
    obs[21] = lon + rng.normal(0.0, 0.01)
    obs[22] = rng.uniform(10_000, 20_000)
    obs[23:26] = rng.uniform(-800, 800, 3)
    return obs


# ------------------------------------------------------------------ tests


def test_the_state_encoder_matches_the_organisers_implementation():
    """Same inputs, same 20 values, over a thousand random frames of a round.

    Fed through float32 first, because that is what the reference does to its
    input and this test is about the algorithm, not the precision.
    """
    rng = np.random.default_rng(20261107)
    encoder = StateEncoder()
    prev = None

    for frame in range(1000):
        obs = np.asarray(random_observation(rng), dtype=np.float32).astype(np.float64)
        expected, prev = reference_get_state(obs, prev)
        actual = encoder.encode(Telemetry.from_observation(obs))
        np.testing.assert_array_equal(actual, expected, err_msg=f"state diverged on frame {frame}")


def test_the_stick_shaping_matches_the_organisers_implementation():
    """Including the rate limit, which depends on every frame before it."""
    rng = np.random.default_rng(7)
    joystick = JoystickState()
    reference_last = joystick.last_command.copy()

    for frame in range(1000):
        obs = random_observation(rng)
        raw = rng.uniform(-1.0, 1.0, 4)
        expected = reference_process_joystick_action(raw, obs, reference_last)
        reference_last = expected
        actual = shape_command(raw, joystick, Telemetry.from_observation(obs).reference_mach)
        np.testing.assert_allclose(
            actual, expected, rtol=0, atol=0, err_msg=f"command diverged on frame {frame}"
        )


def test_the_elevator_limit_is_the_clients_one_not_the_trainers():
    """The trainer's two branches are both 1.0; the client's high branch is 0.4.

    Keeping the client's value is a choice, and a choice is worth pinning: a
    policy trained without the limit meets it for the first time on the day.
    """
    joystick = JoystickState()
    fast = ELEVATOR_LIMIT_ABOVE_MACH + 0.2
    for _ in range(200):  # long enough for the rate limit to reach the stop
        shaped = shape_command(np.array([0.0, 1.0, 0.0, 0.8]), joystick, fast)
    assert shaped[1] == pytest.approx(ELEVATOR_LIMIT_HIGH_SPEED)

    joystick.reset()
    slow = ELEVATOR_LIMIT_ABOVE_MACH - 0.2
    for _ in range(200):
        shaped = shape_command(np.array([0.0, 1.0, 0.0, 0.8]), joystick, slow)
    assert shaped[1] == pytest.approx(1.0)


# ------------------------------------------------- what the rewrite changes


def test_a_new_round_does_not_inherit_the_previous_rounds_geometry():
    """The reference client's `prev_distance` is a module global with no reset.

    Round two's first frame differences against round one's last, and the two
    are wherever the aircraft happened to end and start — a closure rate of
    hundreds of m/s that no training frame ever produced.
    """
    rng = np.random.default_rng(3)
    encoder = StateEncoder()

    far = random_observation(rng)
    far[20] = far[0] + 0.05  # ~5.5 km away
    encoder.encode(Telemetry.from_observation(far))

    near = far.copy()
    near[20] = far[0] + 0.005  # a new round, ~550 m away
    inherited = encoder.encode(Telemetry.from_observation(near))
    assert abs(inherited[13]) > 1.0, "this is the bad value the reference produces"

    encoder.reset()
    clean = encoder.encode(Telemetry.from_observation(near))
    assert clean[13] == 0.0, "the first frame of a round has no closure rate to report"


def test_float64_keeps_the_closure_rate_the_trainer_produced():
    """The reference client rounds to float32 before differencing positions.

    Two points a few hundred metres apart on a 6,378 km radius: the cast costs
    about 0.2 m, and the closure rate multiplies a distance difference by 60.
    """
    obs = np.zeros(26, dtype=np.float64)
    obs[0], obs[1], obs[2] = 22.95, 120.22, 15_000.0
    obs[20], obs[21], obs[22] = 22.95, 120.22, 15_000.0
    obs[13] = 574.0

    closing_m = 2.0
    metres_per_degree = math.radians(1.0) * 6378137.0
    obs[20] = obs[0] + 900.0 / metres_per_degree

    exact = StateEncoder()
    exact.encode(Telemetry.from_observation(obs))
    moved = obs.copy()
    moved[20] = obs[0] + (900.0 - closing_m) / metres_per_degree
    exact_closure = exact.encode(Telemetry.from_observation(moved))[13]

    rounded = StateEncoder()
    rounded.encode(Telemetry.from_observation(obs.astype(np.float32).astype(np.float64)))
    rounded_closure = rounded.encode(Telemetry.from_observation(moved.astype(np.float32).astype(np.float64)))[
        13
    ]

    expected = closing_m * 60.0 / 340.0
    assert exact_closure == pytest.approx(expected, rel=1e-3)
    assert abs(rounded_closure - expected) > abs(exact_closure - expected)


# ------------------------------------------------------------- the wire


def test_an_observation_round_trips_through_the_wire_format():
    rng = np.random.default_rng(11)
    obs = random_observation(rng)
    import struct

    payload = struct.pack("<26d", *obs)
    assert len(payload) == OBS_PACKET_BYTES == 208
    np.testing.assert_array_equal(decode_observation(payload), obs)


def test_a_command_packet_is_thirty_bytes_and_carries_the_trailer():
    payload = encode_command(Command(0.5, -0.25, 0.1, 0.8, PlayerState.CONNECTED))
    assert len(payload) == CMD_PACKET_BYTES == 30
    assert payload.endswith(PLAYER_CMD_TRAILER)
    assert decode_command(payload) == Command(
        pytest.approx(0.5),
        pytest.approx(-0.25),
        pytest.approx(0.1),
        pytest.approx(0.8),
        PlayerState.CONNECTED,
    )


@pytest.mark.parametrize("size", [0, 207, 209, 4096])
def test_a_packet_of_the_wrong_size_is_refused_not_guessed_at(size: int):
    """The reference client drops these. A short OBS is not a degraded frame."""
    with pytest.raises(ProtocolError):
        decode_observation(b"\x00" * size)
