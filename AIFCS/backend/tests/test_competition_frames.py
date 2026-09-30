"""The frames observation (AIFCS 2.0 hypothesis H5, SRC-012's multi-frame idea
on the 26 numbers 表 1 sends)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from competition.environment import (
    OBSERVATIONS,
    EnvConfig,
    build_encoder,
    observation_space,
    observation_width,
)
from competition.features import EXTENDED_STATE_SIZE, ExtendedEncoder
from competition.frames import (
    DEGENERATE,
    FRAME_FIELDS,
    FRAMES,
    FRAMES_EXTRA_SIZE,
    FRAMES_STATE_SIZE,
    PAIRS,
    VECTORS,
    FramesEncoder,
    direction_frame,
)
from competition.mirror import mirror_telemetry, observation_signs
from competition.state import STATE_SIZE, Telemetry

ABOUT_LON = 121.0


def flying(**overrides) -> Telemetry:
    values = dict.fromkeys(Telemetry.__dataclass_fields__, 0.0)
    values.update(
        own_lat_deg=23.0,
        own_lon_deg=ABOUT_LON,
        own_alt_ft=15_000.0,
        own_vn_fps=574.0,
        own_vt_fps=574.0,
        own_vc_fps=574.0,
        own_u_fps=574.0,
        enemy_lat_deg=23.02,
        enemy_lon_deg=ABOUT_LON,
        enemy_alt_ft=15_000.0,
        enemy_vn_fps=574.0,
    )
    values.update(overrides)
    return Telemetry(**values)


def index(vector: str, frame: str, axis: str) -> int:
    return EXTENDED_STATE_SIZE + FRAME_FIELDS.index(f"{vector}@{frame}.{axis}")


def test_the_shape_is_the_extended_thirty_plus_the_named_pairs():
    assert FRAMES_STATE_SIZE == STATE_SIZE + 10 + FRAMES_EXTRA_SIZE
    assert FRAMES_EXTRA_SIZE == 3 * len(PAIRS) == len(FRAME_FIELDS)
    assert len(PAIRS) == len(VECTORS) * len(FRAMES) - len(DEGENERATE) == 25
    assert FramesEncoder().encode(flying()).shape == (FRAMES_STATE_SIZE,)


def test_the_extended_thirty_are_untouched_underneath():
    telemetry = flying(own_roll_deg=30.0, own_pitch_deg=5.0, own_yaw_deg=40.0)
    assert np.allclose(
        FramesEncoder().encode(telemetry)[:EXTENDED_STATE_SIZE], ExtendedEncoder().encode(telemetry)
    )


def test_the_dropped_pairs_are_the_constant_or_present_ones():
    assert ("gravity", "world") in DEGENERATE, "always (0, 0, 1)"
    assert ("los", "los") in DEGENERATE, "always (1, 0, 0)"
    assert ("own_omega", "mybody") in DEGENERATE, "that is p, q, r, already in the reference twenty"
    for pair in DEGENERATE:
        assert pair not in PAIRS


def test_the_line_of_sight_in_the_body_frame_agrees_with_the_reference_angles():
    """The reference twenty carry azimuth and elevation of the target in the
    body frame; the LOS unit vector in that frame has to be their cosines."""
    telemetry = flying(own_roll_deg=20.0, own_pitch_deg=-10.0, own_yaw_deg=75.0, enemy_alt_ft=13_000.0)
    state = FramesEncoder().encode(telemetry)
    azimuth = state[3] * 180.0
    elevation = state[2] * 90.0
    los_body = state[index("los", "mybody", "x") : index("los", "mybody", "x") + 3]
    expected = np.array(
        [
            math.cos(math.radians(elevation)) * math.cos(math.radians(azimuth)),
            math.cos(math.radians(elevation)) * math.sin(math.radians(azimuth)),
            -math.sin(math.radians(elevation)),
        ]
    )
    assert np.allclose(los_body, expected, atol=1e-6)


def test_a_target_dead_ahead_and_level_is_on_every_frames_x_axis():
    telemetry = flying()  # both due north, same altitude, target ahead
    state = FramesEncoder().encode(telemetry)
    for frame in ("mybody", "myvel", "tgtvel"):
        assert np.allclose(
            state[index("los", frame, "x") : index("los", frame, "x") + 3], [1.0, 0.0, 0.0], atol=1e-6
        )
    assert np.allclose(
        state[index("gravity", "myvel", "x") : index("gravity", "myvel", "x") + 3], [0.0, 0.0, 1.0]
    )


def test_gravity_in_the_body_frame_follows_the_bank():
    """Banked right 90 degrees, gravity points along the right wing (+y)."""
    state = FramesEncoder().encode(flying(own_roll_deg=90.0))
    g_body = state[index("gravity", "mybody", "x") : index("gravity", "mybody", "x") + 3]
    assert np.allclose(g_body, [0.0, 1.0, 0.0], atol=1e-6)


def test_a_direction_frame_is_right_handed_with_z_down_and_y_level():
    frame = direction_frame(np.array([1.0, 1.0, -0.5]))
    x, y, z = frame
    assert np.allclose(np.cross(x, y), z)
    assert abs(y[2]) < 1e-9, "roll fixed by gravity: y is horizontal"
    assert z[2] > 0.0, "z has a downward component"
    assert np.allclose(frame @ frame.T, np.eye(3), atol=1e-9)


def test_vertical_and_zero_directions_do_not_produce_nans():
    for direction in (np.array([0.0, 0.0, 1.0]), np.array([0.0, 0.0, -1.0]), np.zeros(3)):
        frame = direction_frame(direction)
        assert np.all(np.isfinite(frame))
        assert np.allclose(frame @ frame.T, np.eye(3), atol=1e-9)
    diving = flying(
        own_vn_fps=0.0, own_vd_fps=574.0, enemy_vn_fps=0.0, enemy_vd_fps=574.0, own_pitch_deg=-90.0
    )
    assert np.all(np.isfinite(FramesEncoder().encode(diving)))


def test_angular_rates_are_squashed():
    state = FramesEncoder().encode(flying(own_p_radps=50.0))
    omega = state[EXTENDED_STATE_SIZE:][
        [
            i - EXTENDED_STATE_SIZE
            for i, name in enumerate(FRAME_FIELDS, EXTENDED_STATE_SIZE)
            if name.startswith("own_omega")
        ]
    ]
    assert np.all(np.abs(omega) <= 1.0)


@pytest.mark.parametrize("seed", range(4))
def test_the_mirror_signs_are_the_encoders_own_mirror(seed: int):
    """Ordinary vectors flip y in every frame; the angular rate flips x and z.
    Checked the only way that counts: against the encoder, on mirrored input."""
    rng = np.random.default_rng(seed)
    straight, mirrored = FramesEncoder(), FramesEncoder()
    signs = observation_signs(FRAMES_STATE_SIZE)
    for _ in range(3):
        telemetry = flying(
            own_lat_deg=23.0 + rng.uniform(-0.03, 0.03),
            own_lon_deg=ABOUT_LON + rng.uniform(-0.03, 0.03),
            own_alt_ft=rng.uniform(9_000, 20_000),
            own_roll_deg=rng.uniform(-160, 160),
            own_pitch_deg=rng.uniform(-50, 50),
            own_yaw_deg=rng.uniform(0, 360),
            own_vn_fps=rng.uniform(-600, 600),
            own_ve_fps=rng.uniform(-600, 600),
            own_vd_fps=rng.uniform(-300, 300),
            own_p_radps=rng.uniform(-3, 3),
            own_q_radps=rng.uniform(-1, 1),
            own_r_radps=rng.uniform(-1, 1),
            own_v_fps=rng.uniform(-60, 60),
            own_w_fps=rng.uniform(-100, 100),
            own_beta_deg=rng.uniform(-8, 8),
            enemy_lat_deg=23.0 + rng.uniform(-0.03, 0.03),
            enemy_lon_deg=ABOUT_LON + rng.uniform(-0.03, 0.03),
            enemy_alt_ft=rng.uniform(9_000, 20_000),
            enemy_vn_fps=rng.uniform(-600, 600),
            enemy_ve_fps=rng.uniform(-600, 600),
            enemy_vd_fps=rng.uniform(-300, 300),
        )
        a = straight.encode(telemetry)
        b = mirrored.encode(mirror_telemetry(telemetry, ABOUT_LON))
        assert np.allclose(b, a * signs, atol=1e-4), np.round(b - a * signs, 5)


def test_the_name_is_routed_everywhere_the_others_are():
    assert OBSERVATIONS == ("reference", "extended", "frames")
    assert isinstance(build_encoder(EnvConfig(observation="frames")), FramesEncoder)
    assert observation_width("frames") == FRAMES_STATE_SIZE
    assert observation_space("frames").shape == (FRAMES_STATE_SIZE,)
    assert (
        observation_width("reference") == STATE_SIZE and observation_width("extended") == EXTENDED_STATE_SIZE
    )
    with pytest.raises(ValueError, match="observation must be one of"):
        observation_width("cubes")
    with pytest.raises(ValueError, match="observation must be one of"):
        build_encoder(EnvConfig(observation="cubes"))


def test_the_trainer_offers_it():
    from competition import train

    assert train.parse_args(["--name", "x", "--observation", "frames"]).observation == "frames"


def test_a_round_flies_with_it():
    from competition.environment import CompetitionRound

    round_ = CompetitionRound(EnvConfig(observation="frames"), seed=3)
    state, _, _, _ = round_.step(np.array([0.0, 0.0, 0.0, 0.8]))
    assert state.shape == (FRAMES_STATE_SIZE,) and np.all(np.isfinite(state))
