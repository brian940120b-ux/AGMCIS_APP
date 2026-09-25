"""The organiser's scoring, checked against the published rules (COMP PHASE 3).

The point of implementing someone else's scoring is to be able to disagree with
your own training reward in a measurable way. The last test here does exactly
that: a policy the reference reward loves scores nothing at all.
"""

from __future__ import annotations

import pytest

from competition.scoring import (
    COLLISION_DISTANCE_M,
    CRASH_ALTITUDE_M,
    G_LIMIT,
    AttackEnvelope,
    EndReason,
    ScoringWeights,
    SideScore,
    Verdict,
    decide_round,
    distance_factor,
    position_advantage,
)
from competition.state import Geometry

FT_TO_M = 0.3048


def geometry(
    *,
    range_ft: float = 1500.0,
    track_deg: float = 0.0,
    aspect_deg: float = 0.0,
    own_alt_m: float = 4500.0,
) -> Geometry:
    return Geometry(
        distance_m=range_ft * FT_TO_M,
        track_angle_deg=track_deg,
        azimuth_deg=0.0,
        elevation_deg=0.0,
        aspect_angle_deg=aspect_deg,
        own_alt_m=own_alt_m,
        enemy_alt_m=own_alt_m,
    )


# --------------------------------------------------------- distance factor


@pytest.mark.parametrize(
    ("distance_m", "expected"),
    [
        (0.0, 0.1),
        (149.9, 0.1),
        (150.0, 1.2),
        (499.9, 1.2),
        (500.0, 1.0),
        (1499.9, 1.0),
        (1500.0, 0.5),
        (2999.9, 0.5),
        (3000.0, 0.1),
        (50_000.0, 0.1),
    ],
)
def test_the_distance_factor_bands_match_the_published_table(distance_m, expected):
    """表 4, and its boundaries, which is where a band table goes wrong."""
    assert distance_factor(distance_m) == expected


def test_closing_inside_150_metres_is_worth_less_than_staying_out():
    """The table prices collision risk below the band it sits under."""
    assert distance_factor(140.0) < distance_factor(300.0)


# ------------------------------------------------------- position advantage


def test_the_best_possible_instant_is_worth_2_point_4():
    """Nose on, directly behind, inside the best-attack band."""
    assert position_advantage(
        geometry(range_ft=300.0 / FT_TO_M, track_deg=0.0, aspect_deg=0.0)
    ) == pytest.approx(2.4)


@pytest.mark.parametrize(("track", "aspect"), [(90.0, 0.0), (0.0, 90.0), (120.0, 150.0)])
def test_beyond_ninety_degrees_a_half_of_the_term_pays_nothing(track, aspect):
    perfect = position_advantage(geometry(range_ft=1000.0, track_deg=0.0, aspect_deg=0.0))
    partial = position_advantage(geometry(range_ft=1000.0, track_deg=track, aspect_deg=aspect))
    assert partial < perfect


def test_a_head_on_merge_pays_for_the_nose_and_nothing_for_the_position():
    """Nose on, but they are pointing straight back at us.

    1000 ft is 305 m, inside the best-attack band, so the track half pays its
    full 1.0 * 1.2. The aspect half pays nothing at 180 degrees: being in front
    of someone is not an advantage, however well aimed.
    """
    assert position_advantage(geometry(range_ft=1000.0, track_deg=0.0, aspect_deg=180.0)) == pytest.approx(
        1.2
    )
    assert position_advantage(geometry(range_ft=1000.0, track_deg=95.0, aspect_deg=180.0)) == pytest.approx(
        0.0
    )


# ------------------------------------------------------------ accumulation


def test_three_seconds_in_the_envelope_is_a_kill():
    score = SideScore()
    inside = geometry(range_ft=1500.0, track_deg=0.5)
    for _ in range(180):  # 3 s at 60 Hz
        score.observe(inside, g_load=1.0)
    assert score.killed
    assert score.killed_at_s == pytest.approx(3.0)


def test_the_three_seconds_do_not_have_to_be_consecutive():
    """The rules say accumulated ("累積")."""
    score = SideScore()
    inside = geometry(range_ft=1500.0, track_deg=0.5)
    outside = geometry(range_ft=1500.0, track_deg=40.0)
    for _ in range(3):
        for _ in range(60):
            score.observe(inside, g_load=1.0)
        for _ in range(120):
            score.observe(outside, g_load=1.0)
    assert score.killed


@pytest.mark.parametrize(
    ("range_ft", "track_deg", "reason"),
    [
        (8000.0, 0.0, "too far"),
        (300.0, 0.0, "too close"),
        (1500.0, 2.0, "outside the cone"),
    ],
)
def test_the_envelope_refuses_what_the_rules_refuse(range_ft, track_deg, reason):
    score = SideScore()
    for _ in range(600):
        score.observe(geometry(range_ft=range_ft, track_deg=track_deg), g_load=1.0)
    assert not score.killed, reason
    assert score.attack_seconds == 0.0


def test_a_faster_kill_scores_more():
    weights = ScoringWeights()
    quick, slow = SideScore(), SideScore()
    inside = geometry(range_ft=1500.0, track_deg=0.0)
    outside = geometry(range_ft=8000.0, track_deg=90.0)

    for _ in range(180):
        quick.observe(inside, g_load=1.0)
    for _ in range(60 * 100):
        slow.observe(outside, g_load=1.0)
    for _ in range(180):
        slow.observe(inside, g_load=1.0)

    assert quick.kill_score > slow.kill_score
    assert quick.kill_score == pytest.approx(weights.kill_base + 300.0 - 3.0)


def test_seconds_above_nine_g_are_subtracted():
    calm, violent = SideScore(), SideScore()
    view = geometry(range_ft=1500.0, track_deg=0.5)
    for _ in range(600):
        calm.observe(view, g_load=4.0)
        violent.observe(view, g_load=G_LIMIT + 0.5)
    assert violent.high_g_seconds == pytest.approx(10.0)
    assert calm.high_g_seconds == 0.0
    assert violent.advantage_score < calm.advantage_score
    assert calm.advantage_score - violent.advantage_score == pytest.approx(10_000.0)


# ----------------------------------------------------------- who won, 表 3


def _scored(attack_s: float = 0.0, position: float = 0.0) -> SideScore:
    score = SideScore()
    score.attack_seconds = attack_s
    score.position_sum = position
    return score


def test_a_kill_beats_any_score():
    """ "無視當下任何分數" — the table says so in as many words."""
    killer = SideScore()
    for _ in range(180):
        killer.observe(geometry(range_ft=1500.0, track_deg=0.0), g_load=1.0)
    rich = _scored(position=100_000.0)

    outcome = decide_round(killer, rich)
    assert outcome.verdict is Verdict.BLUE
    assert outcome.reason is EndReason.KILL
    assert rich.advantage_score > killer.total


def test_a_crash_hands_the_round_to_whoever_is_still_flying():
    outcome = decide_round(_scored(position=50_000.0), _scored(), blue_crashed=True)
    assert outcome.verdict is Verdict.RED
    assert outcome.reason is EndReason.CRASH


def test_a_collision_falls_back_to_the_advantage_score():
    outcome = decide_round(_scored(position=10.0), _scored(position=20.0), collided=True)
    assert outcome.verdict is Verdict.RED
    assert outcome.reason is EndReason.COLLISION


def test_time_running_out_falls_back_to_the_advantage_score():
    outcome = decide_round(_scored(attack_s=2.0), _scored(attack_s=1.0))
    assert outcome.verdict is Verdict.BLUE
    assert outcome.reason is EndReason.TIME


def test_an_exact_tie_is_replayed_rather_than_called():
    outcome = decide_round(_scored(attack_s=1.0), _scored(attack_s=1.0))
    assert outcome.verdict is Verdict.REPLAY


def test_the_published_thresholds_are_the_ones_used():
    assert CRASH_ALTITUDE_M == 50.0
    assert COLLISION_DISTANCE_M == 15.0
    assert G_LIMIT == 9.0
    assert AttackEnvelope().kill_seconds == 3.0


# ------------------------------- where the training reward and the score part


def test_the_reference_reward_pays_for_aim_the_scoring_does_not_count():
    """The reason this module exists.

    `_compute_reward_and_done` pays `(2 - track_angle)` for any frame inside one
    degree, at any range — it has no distance term at all. The competition pays
    only between 500 and 3000 ft. A policy that learns to point its nose from
    two miles away is rewarded all the way through training and scores nothing
    on the day.
    """
    envelope = AttackEnvelope()
    far = geometry(range_ft=8000.0, track_deg=0.2)

    reference_reward = 2.0 - far.track_angle_deg
    assert reference_reward > 1.5, "the training reward pays well for this frame"

    assert not envelope.contains(far), "and the competition counts none of it"

    aimed_from_far = SideScore()
    for _ in range(60 * 300):  # a whole round of perfect aim, out of range
        aimed_from_far.observe(far, g_load=1.0)
    assert aimed_from_far.attack_seconds == 0.0
    assert not aimed_from_far.killed

    # Identical aim inside the envelope: a kill in the first three seconds, and
    # a position term larger by exactly the ratio of the two distance factors.
    # 8000 ft is 2438 m (tactical, 0.5); 1200 ft is 366 m (best attack, 1.2).
    aimed_from_near = SideScore()
    for _ in range(60 * 300):
        aimed_from_near.observe(geometry(range_ft=1200.0, track_deg=0.2), g_load=1.0)

    assert aimed_from_near.killed
    assert aimed_from_near.killed_at_s == pytest.approx(3.0)
    assert aimed_from_near.position_sum / aimed_from_far.position_sum == pytest.approx(1.2 / 0.5)
    assert aimed_from_near.total > aimed_from_far.total
