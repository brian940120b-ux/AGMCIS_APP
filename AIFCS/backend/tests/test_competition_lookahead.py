"""The lookahead observation: the geometry one and three seconds from now.

Dead reckoning along both velocity vectors, expressed in today's body frame.
What is pinned: the width, that it is `extended` plus the predictions, that a
crossing target's predicted azimuth grows with the horizon on the correct
side, that closure has the right sign, and that the mirror flips exactly the
azimuths.
"""

from __future__ import annotations

import numpy as np
import pytest

from competition.environment import OBSERVATIONS, EnvConfig, build_encoder, observation_width
from competition.features import EXTENDED_STATE_SIZE, ExtendedEncoder
from competition.lookahead import (
    HORIZONS_S,
    LOOKAHEAD_STATE_SIZE,
    PER_HORIZON,
    LookaheadEncoder,
    predicted_geometry,
)
from competition.mirror import mirror_observation, observation_signs
from competition.state import Telemetry

M_PER_DEG = 111_320.0


def telemetry(
    *,
    enemy_north_m: float = 2000.0,
    enemy_east_m: float = 0.0,
    enemy_vn_fps: float = 574.0,
    enemy_ve_fps: float = 0.0,
    own_vn_fps: float = 574.0,
) -> Telemetry:
    values = np.zeros(26)
    values[0], values[1], values[2] = 25.0, 121.0, 10_000.0
    values[5] = 0.0  # heading north
    values[6] = own_vn_fps
    values[12] = values[13] = values[15] = own_vn_fps
    values[20] = 25.0 + enemy_north_m / M_PER_DEG
    values[21] = 121.0 + enemy_east_m / (M_PER_DEG * np.cos(np.radians(25.0)))
    values[22] = 10_000.0
    values[23], values[24] = enemy_vn_fps, enemy_ve_fps
    return Telemetry.from_observation(values)


def test_it_is_registered_and_forty_wide():
    assert "lookahead" in OBSERVATIONS
    assert (
        observation_width("lookahead")
        == LOOKAHEAD_STATE_SIZE
        == EXTENDED_STATE_SIZE + PER_HORIZON * len(HORIZONS_S)
    )
    assert isinstance(build_encoder(EnvConfig(observation="lookahead")), LookaheadEncoder)


def test_it_is_extended_then_the_predictions():
    t = telemetry()
    extended = ExtendedEncoder().encode(t)
    lookahead = LookaheadEncoder().encode(t)

    assert lookahead.shape == (LOOKAHEAD_STATE_SIZE,)
    assert np.allclose(lookahead[:EXTENDED_STATE_SIZE], extended)


def test_a_target_flying_away_at_our_speed_stays_where_it_is():
    """Same heading, same speed, dead ahead: the prediction is the present."""
    for seconds in HORIZONS_S:
        azimuth, elevation, range_m, track, closure = predicted_geometry(telemetry(), seconds)
        assert abs(azimuth) < 0.5
        assert abs(elevation) < 0.5
        assert range_m == pytest.approx(2000.0, rel=0.02)
        assert track < 0.5
        assert abs(closure) < 1.0


def test_a_crossing_target_moves_to_the_side_it_is_flying_towards():
    """Target ahead, flying east while we fly north: in one second it is a
    little to the right, in three seconds further right."""
    t = telemetry(enemy_vn_fps=0.0, enemy_ve_fps=574.0)
    az1, _, _, track1, _ = predicted_geometry(t, 1.0)
    az3, _, _, track3, _ = predicted_geometry(t, 3.0)

    assert 0.0 < az1 < az3, "right is positive azimuth, and it grows with the horizon"
    assert track1 < track3


def test_closure_is_positive_when_the_range_is_shrinking():
    closing = telemetry(enemy_vn_fps=-574.0)  # head on
    opening = telemetry(enemy_vn_fps=900.0)  # faster than us, away
    assert predicted_geometry(closing, 1.0)[4] > 300.0
    assert predicted_geometry(opening, 1.0)[4] < -50.0


def test_the_mirror_flips_exactly_the_predicted_azimuths():
    signs = observation_signs(LOOKAHEAD_STATE_SIZE)
    tail = signs[EXTENDED_STATE_SIZE:]
    assert tail.shape == (PER_HORIZON * len(HORIZONS_S),)
    for horizon in range(len(HORIZONS_S)):
        block = tail[horizon * PER_HORIZON : (horizon + 1) * PER_HORIZON]
        assert list(block) == [-1.0, 1.0, 1.0, 1.0, 1.0]

    t = telemetry(enemy_vn_fps=0.0, enemy_ve_fps=574.0)
    seen = LookaheadEncoder().encode(t)
    mirrored = mirror_observation(seen)
    assert mirrored[EXTENDED_STATE_SIZE] == pytest.approx(-seen[EXTENDED_STATE_SIZE])
    assert mirrored[EXTENDED_STATE_SIZE + 2] == pytest.approx(seen[EXTENDED_STATE_SIZE + 2])


def test_reset_clears_the_inherited_rates():
    encoder = LookaheadEncoder()
    encoder.encode(telemetry())
    encoder.encode(telemetry(enemy_east_m=50.0))
    encoder.reset()
    first = encoder.encode(telemetry(enemy_east_m=50.0))
    # The extended azimuth-rate slot is zero on the first frame after a reset.
    assert first[EXTENDED_STATE_SIZE - 5] == 0.0


@pytest.mark.skipif(pytest.importorskip("jsbsim", reason="needs JSBSim") is None, reason="needs JSBSim")
def test_a_round_flies_under_it():
    from competition.environment import CompetitionRound

    game = CompetitionRound(EnvConfig(observation="lookahead"), seed=1)
    observation = game.reset(seed=1)
    assert observation.shape == (LOOKAHEAD_STATE_SIZE,)
    for _ in range(30):
        observation, _geometry, finished, _reason = game.step(np.array([0.0, -0.1, 0.0, 0.9]))
        assert np.all(np.isfinite(observation))
        if finished:
            break
