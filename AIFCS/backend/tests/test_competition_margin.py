"""The margin and shaped rewards (COMP PHASE 14).

`score` is the competition's own number for one side. It is the right thing to
*measure* with and, as the first test here records, the wrong thing to train
on: it never punishes anything except 9G, so a policy that flies into the
ground collects a positive return on the way down.

What the rules actually compare, when a round reaches time, is
作戰優勢分高者勝 — whose advantage score is larger. That is a difference, and
it is what `margin` optimises.
"""

from __future__ import annotations

import numpy as np
import pytest

from competition.rewards import ScoreReward, ShapedReward
from competition.scoring import AttackEnvelope, ScoringWeights
from competition.state import Geometry

pytest.importorskip("jsbsim")

from competition.action import INITIAL_THROTTLE
from competition.environment import EnvConfig
from competition.gym_env import CompetitionEnv


def at(distance_m: float, track: float = 0.0, aspect: float = 0.0) -> Geometry:
    return Geometry(
        distance_m=distance_m,
        track_angle_deg=track,
        azimuth_deg=track,
        elevation_deg=0.0,
        aspect_angle_deg=aspect,
        own_alt_m=5000.0,
        enemy_alt_m=5000.0,
    )


def build(kind: str) -> ScoreReward | ShapedReward:
    weights, envelope = ScoringWeights(), AttackEnvelope()
    if kind == "shaped":
        return ShapedReward(weights=weights, envelope=envelope, tick_hz=60.0)
    return ScoreReward(weights=weights, envelope=envelope, tick_hz=60.0, defensive=kind == "margin")


# ----------------------------------------------------- what the score misses


def test_the_plain_score_pays_a_policy_for_flying_into_the_ground():
    """Measured, not argued: a centred stick, every frame to the crash.

    Position advantage is never negative and the crash costs -10 once, so 2,928
    frames of it outweigh the ending. This is why `--reward score` was the
    wrong recommendation and `margin` replaced it.
    """
    hold = np.array([0.0, 0.0, 0.0, INITIAL_THROTTLE])
    totals = {}
    for mode in ("score", "margin", "shaped"):
        env = CompetitionEnv(EnvConfig(), reward_mode=mode, seed=100)
        env.reset(seed=100)
        total = 0.0
        while True:
            _, reward, terminated, truncated, info = env.step(hold)
            total += reward
            if terminated or truncated:
                break
        assert info["reason"] == "CRASH"
        totals[mode] = total

    assert totals["score"] > 0, "the finding this test exists to record"
    assert totals["margin"] < 0, "the opponent keeps scoring while we dive"
    assert totals["shaped"] < 0


# ---------------------------------------------------------------- the margin


def test_the_margin_is_the_difference_the_rules_compare():
    """Denying a point is worth the same as scoring one."""
    margin = build("margin")
    ours, theirs = at(400.0, track=0.0, aspect=0.0), at(400.0, track=30.0, aspect=40.0)

    mine = margin.frame_advantage(ours, 1.0)
    yours = margin.frame_advantage(theirs, 1.0)
    reward = margin(ours, g_load=1.0, crashed=False, foe_crashed=False, foe_geometry=theirs)

    assert reward == pytest.approx((mine - yours) / margin.scale)


def test_a_symmetric_frame_is_worth_nothing_to_either_side():
    margin = build("margin")
    same = at(400.0, track=10.0, aspect=10.0)
    reward = margin(same, g_load=1.0, crashed=False, foe_crashed=False, foe_geometry=same)
    assert reward == pytest.approx(0.0)


def test_the_plain_score_ignores_the_opponent_entirely():
    """`score` has to stay the competition's own one-sided number, because that
    is what makes it the honest thing to measure with."""
    score = build("score")
    ours, theirs = at(400.0), at(400.0, track=0.0, aspect=0.0)
    with_foe = score(ours, g_load=1.0, crashed=False, foe_crashed=False, foe_geometry=theirs)
    without = score(ours, g_load=1.0, crashed=False, foe_crashed=False)
    assert with_foe == without


# --------------------------------------------------------------- the shaping


def test_the_attack_term_alone_gives_a_policy_no_direction():
    """Why `shaped` exists.

    The competition's attack term is an indicator on a one-degree cone: 2000 a
    second inside, nothing outside, no slope between. Two frames that are 90
    and 30 degrees off are worth exactly the same to it, so nothing tells a
    policy that 30 is the better of the two.
    """
    score = build("score")
    far_off = score(at(400.0, track=90.0), g_load=1.0, crashed=False, foe_crashed=False)
    closer = score(at(400.0, track=30.0), g_load=1.0, crashed=False, foe_crashed=False)
    assert closer > far_off, "position advantage does slope"

    # But the attack term itself does not, and it is worth 2000 against 10.
    envelope = AttackEnvelope()
    assert not envelope.contains(at(400.0, track=90.0))
    assert not envelope.contains(at(400.0, track=30.0))
    assert not envelope.contains(at(400.0, track=1.5)), "still nothing at 1.5 degrees"


def test_the_shaped_reward_slopes_towards_the_cone_all_the_way_in():
    shaped = build("shaped")
    values = [
        shaped(at(400.0, track=angle), g_load=1.0, crashed=False, foe_crashed=False)
        for angle in (90.0, 45.0, 10.0, 2.0)
    ]
    assert values == sorted(values), "every step towards the nose is worth more"


def test_the_range_window_is_the_published_envelope():
    shaped = build("shaped")
    assert shaped.range_factor(1500.0) == 1.0, "inside 500-3000 ft"
    assert shaped.range_factor(500.0) == 1.0
    assert shaped.range_factor(3000.0) == 1.0
    assert shaped.range_factor(250.0) < 1.0, "too close to shoot"
    assert shaped.range_factor(6000.0) < 0.5, "too far to matter"


def test_overshooting_is_punished_where_the_score_only_shrugs():
    """Inside 150 m the distance factor collapses to a twelfth and a collision
    ends the round for both — but nothing in the score says "do not"."""
    shaped = build("shaped")
    close = shaped(at(100.0, track=0.0), g_load=1.0, crashed=False, foe_crashed=False)
    good = shaped(at(300.0, track=0.0), g_load=1.0, crashed=False, foe_crashed=False)
    assert close < good
