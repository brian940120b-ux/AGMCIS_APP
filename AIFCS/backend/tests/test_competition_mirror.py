"""Mirroring transitions (AIFCS 2.0, hypothesis H1 from docs/PRIOR_ART.md 6.6).

Three things have to be true for a mirrored transition to be worth storing:
the observation's sign map is the mirror of the encoder (checked against the
encoder itself, on both encodings, including the terms with memory); the action
map keeps a pinned rudder pinned; and the aeroplane is symmetric enough that
the twin is a transition that could have happened. The last one is measured
against JSBSim rather than assumed.
"""

from __future__ import annotations

import numpy as np
import pytest

from competition.environment import action_space, observation_space
from competition.features import EXTENDED_STATE_SIZE, ExtendedEncoder
from competition.mirror import (
    ACTION_SIGNS,
    EXTENDED_SIGNS,
    REFERENCE_SIGNS,
    MirroredReplayBuffer,
    mirror_action_scaled,
    mirror_observation,
    mirror_telemetry,
    observation_signs,
)
from competition.state import STATE_SIZE, StateEncoder, Telemetry

ABOUT_LON = 121.0


def frames(seed: int, count: int = 3) -> list[Telemetry]:
    """A few consecutive-looking frames of a busy fight, every field non-zero."""
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(count):
        values = {
            "own_lat_deg": 23.0 + rng.uniform(-0.05, 0.05),
            "own_lon_deg": ABOUT_LON + rng.uniform(-0.05, 0.05),
            "own_alt_ft": rng.uniform(8_000, 20_000),
            "own_roll_deg": rng.uniform(-170, 170),
            "own_pitch_deg": rng.uniform(-60, 60),
            "own_yaw_deg": rng.uniform(0, 360),
            "own_vn_fps": rng.uniform(-600, 600),
            "own_ve_fps": rng.uniform(-600, 600),
            "own_vd_fps": rng.uniform(-300, 300),
            "own_p_radps": rng.uniform(-3, 3),
            "own_q_radps": rng.uniform(-1, 1),
            "own_r_radps": rng.uniform(-1, 1),
            "own_vc_fps": rng.uniform(300, 700),
            "own_vt_fps": rng.uniform(400, 900),
            "own_g_acc": rng.uniform(-9, 3),
            "own_u_fps": rng.uniform(400, 900),
            "own_v_fps": rng.uniform(-80, 80),
            "own_w_fps": rng.uniform(-150, 150),
            "own_alpha_deg": rng.uniform(-5, 25),
            "own_beta_deg": rng.uniform(-10, 10),
            "enemy_lat_deg": 23.0 + rng.uniform(-0.05, 0.05),
            "enemy_lon_deg": ABOUT_LON + rng.uniform(-0.05, 0.05),
            "enemy_alt_ft": rng.uniform(8_000, 20_000),
            "enemy_vn_fps": rng.uniform(-600, 600),
            "enemy_ve_fps": rng.uniform(-600, 600),
            "enemy_vd_fps": rng.uniform(-300, 300),
        }
        out.append(Telemetry(**values))
    return out


@pytest.mark.parametrize("seed", range(6))
def test_the_reference_signs_are_the_encoders_own_mirror(seed: int):
    """encode(mirror(frame)) == signs * encode(frame), frame after frame.

    Frame after frame because closure rate carries the previous distance: a
    mirrored *sequence* has to encode to the mirrored sequence, not just one
    frame in isolation.
    """
    straight, mirrored = StateEncoder(), StateEncoder()
    for frame in frames(seed):
        a = straight.encode(frame)
        b = mirrored.encode(mirror_telemetry(frame, ABOUT_LON))
        assert np.allclose(b, a * REFERENCE_SIGNS, atol=1e-5), np.round(b - a * REFERENCE_SIGNS, 6)


@pytest.mark.parametrize("seed", range(6))
def test_the_extended_signs_are_the_extended_encoders_mirror(seed: int):
    """The ten extras carry rates with memory too: azimuth rate flips, the
    others (elevation, aspect, the enemy's unsigned turn rate, time) hold."""
    straight, mirrored = ExtendedEncoder(), ExtendedEncoder()
    for frame in frames(seed):
        a = straight.encode(frame)
        b = mirrored.encode(mirror_telemetry(frame, ABOUT_LON))
        assert np.allclose(b, a * EXTENDED_SIGNS, atol=1e-5), np.round(b - a * EXTENDED_SIGNS, 6)


def test_the_map_is_chosen_by_width_and_nothing_else_has_one():
    assert observation_signs(STATE_SIZE) is REFERENCE_SIGNS
    assert observation_signs(EXTENDED_STATE_SIZE) is EXTENDED_SIGNS
    with pytest.raises(ValueError, match="no mirror map for a 26-wide"):
        observation_signs(26)


def test_mirroring_is_its_own_inverse_on_a_batch():
    batch = np.random.default_rng(1).uniform(-1, 1, size=(5, EXTENDED_STATE_SIZE)).astype(np.float32)
    assert np.array_equal(mirror_observation(mirror_observation(batch)), batch)


def test_aileron_and_rudder_flip_and_the_rest_do_not():
    assert list(ACTION_SIGNS) == [-1.0, 1.0, -1.0, 1.0]
    space = action_space(rudder_enabled=True)
    scaled = np.array([[0.3, -0.7, 0.5, 0.2]])
    assert np.allclose(mirror_action_scaled(scaled, space), [[-0.3, -0.7, -0.5, 0.2]])


def test_a_pinned_rudder_stays_pinned_in_the_mirror():
    """The reference pins the rudder to [0, 1e-17], so a rudder of 0 is stored
    as -1 in scaled units. A naive sign flip would store +1: full rudder,
    which the aircraft was never flown with."""
    space = action_space(rudder_enabled=False)
    # What SB3 stores for a policy that outputs rudder 0: scaled to the box.
    stored = np.array([[0.3, -0.7, -1.0, 0.2]])
    mirrored = mirror_action_scaled(stored, space)
    assert np.allclose(mirrored, [[-0.3, -0.7, -1.0, 0.2]])
    # And a channel with a degenerate box never divides by zero.
    assert np.all(np.isfinite(mirrored))


def test_the_buffer_stores_every_transition_and_its_twin():
    obs_space, act_space = observation_space("extended"), action_space(rudder_enabled=False)
    buffer = MirroredReplayBuffer(8, obs_space, act_space, n_envs=1)
    rng = np.random.default_rng(2)
    obs = rng.uniform(-1, 1, size=(1, EXTENDED_STATE_SIZE)).astype(np.float32)
    nxt = rng.uniform(-1, 1, size=(1, EXTENDED_STATE_SIZE)).astype(np.float32)
    act = np.array([[0.4, -0.2, -1.0, 0.9]], dtype=np.float32)
    buffer.add(obs, nxt, act, np.array([1.5]), np.array([False]), [{}])

    assert buffer.size() == 2, "one add, two entries"
    assert np.array_equal(buffer.observations[0, 0], obs[0])
    assert np.allclose(buffer.observations[1, 0], obs[0] * EXTENDED_SIGNS)
    assert np.allclose(buffer.next_observations[1, 0], nxt[0] * EXTENDED_SIGNS)
    assert np.allclose(buffer.actions[1, 0], [-0.4, -0.2, -1.0, 0.9])
    assert buffer.rewards[1, 0] == buffer.rewards[0, 0] == 1.5
    sample = buffer.sample(2)
    assert sample.observations.shape == (2, EXTENDED_STATE_SIZE)


def test_the_buffer_refuses_an_observation_it_has_no_map_for():
    from gymnasium.spaces import Box

    odd = Box(low=-1.0, high=1.0, shape=(26,), dtype=np.float32)
    with pytest.raises(ValueError, match="no mirror map"):
        MirroredReplayBuffer(8, odd, action_space(False), n_envs=1)


def test_the_aeroplane_is_symmetric_enough_per_frame_for_a_twin_to_be_honest():
    """Measured against JSBSim, the bound competition/mirror.py claims.

    Two F-16s, mirrored headings, one second of mirrored random full-authority
    stick. After sixty frames the two are still each other's reflection to a
    fifth of a degree of bank and a metre of position. The divergence that
    appears after ten seconds (twelve degrees) is chaos amplifying a tiny
    model asymmetry, not a bias — with the lateral stick centred the pair stay
    within 0.001 degrees a second — and a stored transition spans one frame.
    """
    from competition.action import JoystickState, shape_command
    from competition.environment import Aircraft

    def fly(heading: float, sign: float) -> Telemetry:
        rng = np.random.default_rng(7)

        def aircraft(lat_deg: float) -> Aircraft:
            return Aircraft(
                root=None,
                lat_deg=lat_deg,
                lon_deg=ABOUT_LON,
                altitude_ft=15_000.0,
                heading_deg=heading,
                speed_kcas=340.0,
            )

        own, foe = aircraft(24.0), aircraft(24.02)
        stick = JoystickState()
        for _ in range(60):
            raw = rng.uniform(-1, 1, size=4)
            raw[3] = 0.8
            raw[0] *= sign
            raw[2] *= sign
            telemetry = own.telemetry_against(foe)
            own.apply(shape_command(raw, stick, telemetry.reference_mach))
            own.step()
            foe.apply(np.array([0.0, 0.0, 0.0, 0.8]))
            foe.step()
        return own.telemetry_against(foe)

    a, b = fly(30.0, +1.0), fly(330.0, -1.0)
    assert abs(a.own_roll_deg + b.own_roll_deg) < 0.2
    assert abs(a.own_pitch_deg - b.own_pitch_deg) < 0.2
    assert abs(((a.own_yaw_deg + b.own_yaw_deg + 180.0) % 360.0) - 180.0) < 0.1
    assert abs(a.own_v_fps + b.own_v_fps) < 3.0
    assert abs(a.own_beta_deg + b.own_beta_deg) < 0.3
    north_m = (a.own_lat_deg - b.own_lat_deg) * 111_000.0
    east_offsets = (a.own_lon_deg - ABOUT_LON) + (b.own_lon_deg - ABOUT_LON)
    east_m = east_offsets * 111_000.0 * np.cos(np.radians(24.0))
    assert abs(north_m) < 1.0 and abs(east_m) < 1.0
    assert abs(a.own_alt_ft - b.own_alt_ft) * 0.3048 < 1.0
