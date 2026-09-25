"""The two rewards, and where they part (COMP PHASE 5b).

A reward is a claim about what matters, and this package contains two different
claims. These tests pin each one to its source and then show the case that
separates them, because that case is the reason for having both.
"""

from __future__ import annotations

import pytest

from competition.rewards import ReferenceReward, RewardMode, ScoreReward, sigmoid
from competition.scoring import AttackEnvelope, ScoringWeights
from competition.state import Geometry

FT_TO_M = 0.3048


def geometry(
    *,
    range_ft: float = 1500.0,
    track_deg: float = 0.0,
    aspect_deg: float = 0.0,
    azimuth_deg: float = 0.0,
    elevation_deg: float = 0.0,
    own_alt_m: float = 4500.0,
) -> Geometry:
    return Geometry(
        distance_m=range_ft * FT_TO_M,
        track_angle_deg=track_deg,
        azimuth_deg=azimuth_deg,
        elevation_deg=elevation_deg,
        aspect_angle_deg=aspect_deg,
        own_alt_m=own_alt_m,
        enemy_alt_m=own_alt_m,
    )


def score_reward() -> ScoreReward:
    return ScoreReward(weights=ScoringWeights(), envelope=AttackEnvelope())


# ---------------------------------------------------------------- reference


def test_the_sigmoid_matches_the_references_two_branches():
    """Written branch for branch, because it is written that way to stay stable."""
    assert sigmoid(0.0, 1.0, 0.0) == pytest.approx(0.5)
    assert sigmoid(1000.0, 1.0, 0.0) == pytest.approx(1.0)
    assert sigmoid(-1000.0, 1.0, 0.0) == pytest.approx(0.0)


def test_the_reference_pays_for_a_nose_on_target_at_any_range():
    """No distance term at all: the defect the score reward exists to answer."""
    reward = ReferenceReward()
    near = reward(geometry(range_ft=1500.0, track_deg=0.2), crashed=False, foe_crashed=False)
    reward.reset()
    far = reward(geometry(range_ft=20_000.0, track_deg=0.2), crashed=False, foe_crashed=False)
    assert near == pytest.approx(far, abs=0.01)


def test_the_reference_penalises_the_deck():
    reward = ReferenceReward()
    high = reward(geometry(own_alt_m=6000.0, track_deg=45.0), crashed=False, foe_crashed=False)
    reward.reset()
    low = reward(geometry(own_alt_m=150.0, track_deg=45.0), crashed=False, foe_crashed=False)
    assert low < high - 4.0


def test_the_reference_pays_for_closing_mostly_when_far_away():
    """Its weight is a sigmoid about 2,900 ft: nearly off inside, full beyond."""
    far = ReferenceReward()
    far(geometry(range_ft=8000.0, track_deg=45.0), crashed=False, foe_crashed=False)
    far_gain = far(geometry(range_ft=7990.0, track_deg=45.0), crashed=False, foe_crashed=False)

    near = ReferenceReward()
    near(geometry(range_ft=1500.0, track_deg=45.0), crashed=False, foe_crashed=False)
    near_gain = near(geometry(range_ft=1490.0, track_deg=45.0), crashed=False, foe_crashed=False)

    assert far_gain > near_gain


def test_an_opponent_falling_out_of_the_sky_is_worth_nothing():
    """Both rewards refuse it, so neither can learn to wait."""
    assert ReferenceReward()(geometry(), crashed=False, foe_crashed=True) == 0.0
    assert score_reward()(geometry(), g_load=1.0, crashed=False, foe_crashed=True) == 0.0


def test_losing_the_aircraft_costs_the_same_in_both():
    assert ReferenceReward()(geometry(), crashed=True, foe_crashed=False) == -10.0
    assert score_reward()(geometry(), g_load=1.0, crashed=True, foe_crashed=False) == -10.0


# -------------------------------------------------------------------- score


def test_the_score_reward_pays_only_inside_the_envelope():
    reward = score_reward()
    inside = reward(geometry(range_ft=1500.0, track_deg=0.2), g_load=1.0, crashed=False, foe_crashed=False)
    outside = reward(geometry(range_ft=8000.0, track_deg=0.2), g_load=1.0, crashed=False, foe_crashed=False)
    assert inside > outside


def test_the_score_reward_subtracts_for_over_nine_g():
    reward = score_reward()
    calm = reward(geometry(track_deg=0.2), g_load=4.0, crashed=False, foe_crashed=False)
    violent = reward(geometry(track_deg=0.2), g_load=9.5, crashed=False, foe_crashed=False)
    assert calm - violent == pytest.approx(1000.0 / 60.0 / 10.0)


def test_the_kill_bonus_is_paid_once_and_favours_speed():
    reward = score_reward()
    view = geometry(range_ft=1500.0, track_deg=0.2)
    quick = reward(view, g_load=1.0, crashed=False, foe_crashed=False, killed_at_s=3.0)
    slow = reward(view, g_load=1.0, crashed=False, foe_crashed=False, killed_at_s=250.0)
    nothing = reward(view, g_load=1.0, crashed=False, foe_crashed=False, killed_at_s=None)
    assert quick > slow > nothing


# --------------------------------------------------- where they disagree


def test_a_policy_optimal_for_one_reward_is_not_for_the_other():
    """Perfect aim from 8,000 ft against imperfect aim from 1,200 ft.

    The reference prefers the first, because aim is all it measures. The
    competition prefers the second, because the first is out of range and it
    counts none of it. There is no wording that resolves this — only a run.
    """
    far_and_perfect = geometry(range_ft=8000.0, track_deg=0.0, aspect_deg=10.0)
    near_and_rough = geometry(range_ft=1200.0, track_deg=0.8, aspect_deg=10.0)

    reference = ReferenceReward()
    reference_far = reference(far_and_perfect, crashed=False, foe_crashed=False)
    reference.reset()
    reference_near = reference(near_and_rough, crashed=False, foe_crashed=False)

    scored = score_reward()
    score_far = scored(far_and_perfect, g_load=1.0, crashed=False, foe_crashed=False)
    score_near = scored(near_and_rough, g_load=1.0, crashed=False, foe_crashed=False)

    assert reference_far > reference_near, "the reference prefers the aim"
    assert score_near > score_far, "the competition prefers the range"


def test_the_modes_are_named_the_way_the_command_line_names_them():
    assert RewardMode("reference") is RewardMode.REFERENCE
    assert RewardMode("score") is RewardMode.SCORE
