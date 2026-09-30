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


# ------------------------------------------- the steep near-cone term (v7)


def _geometry_at(track_deg: float, distance_m: float = 300.0):
    """A frame with the nose this far off, inside the firing range."""
    return Geometry(
        distance_m=distance_m,
        track_angle_deg=track_deg,
        azimuth_deg=track_deg,
        elevation_deg=0.0,
        aspect_angle_deg=0.0,
        own_alt_m=3000.0,
        enemy_alt_m=3000.0,
    )


def _pointed_minus_shaped(track_deg: float) -> float:
    """What the new term alone pays at this track angle."""
    from competition.rewards import PointedReward, ShapedReward
    from competition.scoring import AttackEnvelope, ScoringWeights

    geometry = _geometry_at(track_deg)
    pointed_reward = PointedReward(weights=ScoringWeights(), envelope=AttackEnvelope(), scale=1.0)
    shaped_reward = ShapedReward(weights=ScoringWeights(), envelope=AttackEnvelope(), scale=1.0)
    pointed = pointed_reward(geometry, g_load=1.0, crashed=False, foe_crashed=False)
    shaped = shaped_reward(geometry, g_load=1.0, crashed=False, foe_crashed=False)
    return pointed - shaped


def test_shaped_is_unchanged_so_v6_stays_reproducible():
    """The one good result on the board trained under `shaped`. Adding the new
    term to it rather than beside it would have made v6 unrepeatable and the
    comparison meaningless."""
    from competition.rewards import ShapedReward
    from competition.scoring import AttackEnvelope, ScoringWeights

    reward = ShapedReward(weights=ScoringWeights(), envelope=AttackEnvelope())

    assert reward.fine_tracking == 0.0


def test_the_new_term_pays_where_the_linear_one_is_flat():
    """The measured failure. `shaped` pays +0.278 of its tracking term to close
    from 90 degrees to 40, and +0.022 for the whole of five degrees down to
    one — so the coarse turn is worth more than the shot, and the span that
    decides whether anything scores has almost no gradient in it. v6 reaches
    0.0 degrees, drifts at only 3.2 deg/s, and still spends 3.8% of its close
    time inside one degree, which is what chance alone would give."""
    at_five = _pointed_minus_shaped(5.0)
    at_one = _pointed_minus_shaped(1.0)

    assert at_one > at_five, "closer has to be worth more"
    # The linear term pays 0.022 of its weight over this span. This one has to
    # pay enough to be seen next to it, not merely more.
    assert (at_one - at_five) > 20 * 0.0222 * 400.0 / 60.0


def test_the_new_term_leaves_the_coarse_turn_alone():
    """It is an addition near the cone, not a reweighting of the whole turn. At
    forty degrees it must be indistinguishable from zero, or it would move the
    behaviour v6 already does well."""
    assert _pointed_minus_shaped(40.0) == pytest.approx(0.0, abs=1e-6)


def test_the_new_term_is_a_slope_not_a_step():
    """PHANG-MAN's is a logistic of steepness 1e5, which is a step at the cone
    edge. A step hands back the same flat gradient one degree further out, so
    this one has a real width: it has to be climbing at three degrees, where a
    policy that has never scored actually is."""
    three = _pointed_minus_shaped(3.0)
    two = _pointed_minus_shaped(2.0)
    one = _pointed_minus_shaped(1.0)

    assert two - three > 0.2 * (one - three), "the climb starts well outside the cone"


# ---------------------------------------- potential-based shaping (H4)


def _potential():
    from competition.rewards import PotentialReward

    return PotentialReward(weights=ScoringWeights(), envelope=AttackEnvelope(), scale=1.0, tick_hz=60.0)


def _pay(reward, geometry, foe=None):
    return reward(geometry, g_load=1.0, crashed=False, foe_crashed=False, foe_geometry=foe)


def _base_only(reward, geometry):
    return reward._base(geometry, g_load=1.0, crashed=False, foe_crashed=False)


def test_potential_is_a_mode_with_a_research_tier():
    from competition.experiments import REWARD_TIERS, reward_tier_of
    from competition.rewards import RewardMode

    assert RewardMode("potential") is RewardMode.POTENTIAL
    assert REWARD_TIERS["potential"] == "research"
    assert reward_tier_of("--reward potential") == "research"


def test_the_first_frame_of_a_round_pays_no_difference():
    reward = _potential()
    geometry = _geometry_at(0.5)
    assert _pay(reward, geometry) == pytest.approx(_base_only(reward, geometry))


def test_holding_the_cone_earns_the_official_term_and_no_shaping():
    """Sit in the cone frame after frame: the shaping difference is zero and
    what is paid is exactly `shaped` with its tracking term switched off —
    the official 2,000 a second, and nothing for looking good."""
    reward = _potential()
    geometry = _geometry_at(0.5)
    _pay(reward, geometry)
    second = _pay(reward, geometry)
    base = _base_only(reward, geometry)
    assert second == pytest.approx(base)
    assert base > 0.0, "the attack term is in there"


def test_improving_the_aim_is_paid_and_losing_it_is_charged_the_same():
    reward = _potential()
    far, near = _geometry_at(90.0), _geometry_at(1.0)
    _pay(reward, far)
    gained = _pay(reward, near) - _base_only(reward, near)
    lost = _pay(reward, far) - _base_only(reward, far)
    assert gained > 0.0
    assert lost == pytest.approx(-gained)


def test_the_shaping_telescopes_so_passing_through_six_times_pays_like_once():
    """Whatever the path, the shaping sums to Phi(end) - Phi(start)."""
    reward = _potential()

    def shaping_sum(track_angles: list[float]) -> float:
        reward.reset()
        total = 0.0
        for angle in track_angles:
            geometry = _geometry_at(angle)
            total += _pay(reward, geometry) - _base_only(reward, geometry)
        return total

    once = shaping_sum([90.0, 45.0, 10.0, 0.5])
    six_times = shaping_sum([90.0, 0.5, 40.0, 0.5, 60.0, 0.5, 30.0, 0.5, 80.0, 0.5, 20.0, 0.5])
    assert once == pytest.approx(six_times)
    assert once == pytest.approx(reward.phi(_geometry_at(0.5)) - reward.phi(_geometry_at(90.0)))


def test_the_potentials_full_scale_is_one_second_of_the_attack_term():
    reward = _potential()
    assert reward.phi(_geometry_at(0.0)) == pytest.approx(ScoringWeights().attack_time)
    assert reward.phi(_geometry_at(180.0)) == pytest.approx(0.0)
    assert reward.potential == 2000.0


def test_the_opponents_geometry_subtracts_symmetrically():
    reward = _potential()
    geometry = _geometry_at(10.0)
    assert reward.phi(geometry, geometry) == pytest.approx(0.0), "same aim both ways is no advantage"
    assert reward.phi(_geometry_at(0.0), _geometry_at(90.0)) > 0.0
    assert reward.phi(_geometry_at(90.0), _geometry_at(0.0)) < 0.0


def test_reset_forgets_the_last_round():
    reward = _potential()
    _pay(reward, _geometry_at(0.5))
    reward.reset()
    far = _geometry_at(90.0)
    assert _pay(reward, far) == pytest.approx(_base_only(reward, far))


def test_crashes_are_the_same_as_in_shaped():
    reward = _potential()
    assert reward(_geometry_at(0.5), g_load=1.0, crashed=True, foe_crashed=False) == -10.0
    assert reward(_geometry_at(0.5), g_load=1.0, crashed=False, foe_crashed=True) == 0.0


def test_the_deck_is_still_charged_per_frame():
    reward = _potential()
    high = _geometry_at(90.0)
    low = Geometry(
        distance_m=300.0,
        track_angle_deg=90.0,
        azimuth_deg=90.0,
        elevation_deg=0.0,
        aspect_angle_deg=0.0,
        own_alt_m=200.0,
        enemy_alt_m=200.0,
    )
    reward.reset()
    at_height = _pay(reward, high)
    reward.reset()
    assert _pay(reward, low) < at_height


def test_the_gym_env_builds_it():
    from competition.environment import EnvConfig
    from competition.gym_env import CompetitionEnv
    from competition.rewards import PotentialReward

    env = CompetitionEnv(EnvConfig(), reward_mode="potential", seed=1)
    assert isinstance(env._reward, PotentialReward)
