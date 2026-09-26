"""Measuring a policy the way the day measures it (COMP PHASE 13).

Until this existed there was no repeatable number to tune against: training
reports a reward, the competition reports a verdict, and `rewards.py` exists
precisely because those are different functions. These tests are about the
measuring instrument, so they check the properties that make a measurement
worth trusting — same seeds give the same answer, the rates count what they
say they count — rather than any particular policy being good.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("jsbsim")

from competition.action import INITIAL_THROTTLE
from competition.environment import EnvConfig
from competition.evaluate import SWEET_SPOT_M, evaluate, neutral_policy
from competition.scoring import AttackEnvelope, distance_factor
from competition.state import Geometry


def _at(distance_m: float) -> Geometry:
    """Geometry with the nose exactly on, at a chosen range."""
    return Geometry(
        distance_m=distance_m,
        track_angle_deg=0.0,
        azimuth_deg=0.0,
        elevation_deg=0.0,
        aspect_angle_deg=0.0,
        own_alt_m=5000.0,
        enemy_alt_m=5000.0,
    )


def test_the_sweet_spot_is_where_the_two_rules_overlap():
    """Arithmetic, not opinion, and the one place the scoring rewards a choice.

    The attack envelope opens at 500 ft (152 m) and the distance factor is at
    its highest, 1.2, from 150 m to 500 m. Between 152 m and 500 m a policy is
    therefore both able to score a kill and earning position points at the best
    rate available. Outside it, one of the two is always giving something up.
    """
    low, high = SWEET_SPOT_M
    envelope = AttackEnvelope()

    # The lower edge is the envelope's, not the band's. The two do not line up:
    # the 1.2 band opens at 150 m and the envelope at 152.4 m, leaving 2.4 m
    # where the position score is at its best and a shot is still too close to
    # count. Worth knowing rather than rounding away — the rules use metres for
    # one and feet for the other, and that is where the sliver comes from.
    assert low == pytest.approx(envelope.min_range_ft * 0.3048, abs=0.01)
    assert distance_factor(149.0) == 0.1, "below the band, position is worth a twelfth"
    assert distance_factor(151.0) == 1.2, "the band opens before the envelope does"
    assert not envelope.contains(_at(151.0)), "and a shot there is too close to count"

    assert distance_factor((low + high) / 2) == 1.2, "inside, the band pays the most"
    assert distance_factor(high + 1) == 1.0, "above it, position is worth less"
    # And every metre of the sweet spot is a firing solution.
    assert envelope.min_range_ft <= low / 0.3048 + 0.001
    assert high / 0.3048 <= envelope.max_range_ft


def test_the_same_seed_gives_the_same_round(tmp_path):
    """A measurement that moves on its own cannot settle an argument."""
    config = EnvConfig()
    first = evaluate(neutral_policy(), config, rounds=2, seed=7, label="a")
    second = evaluate(neutral_policy(), config, rounds=2, seed=7, label="b")

    assert [r.frames for r in first.rounds] == [r.frames for r in second.rounds]
    assert first.mean_margin == second.mean_margin
    assert [r.outcome.verdict for r in first.rounds] == [r.outcome.verdict for r in second.rounds]


def test_different_seeds_give_different_rounds():
    """The counterpart: identical results across seeds would mean the seed is
    not reaching the initial conditions, and every round would be one round."""
    config = EnvConfig()
    report = evaluate(neutral_policy(), config, rounds=3, seed=11)
    assert len({r.frames for r in report.rounds}) > 1


def test_doing_nothing_flies_into_the_ground():
    """Worth pinning, because it is the environment's most important property.

    A centred stick is not level flight here: the aircraft is initialised
    without trim, exactly as the reference package initialises it, and it
    pitches down and accelerates into the ground inside a minute. Every policy
    has to spend some of its learning on staying airborne before any of it can
    go on fighting, which is the likeliest reason an undertrained policy scores
    zero kills and crashes in every round.
    """
    report = evaluate(neutral_policy(), EnvConfig(), rounds=3, seed=100, label="neutral")

    assert report.crash_rate == 1.0
    assert report.win_rate == 0.0
    assert all(r.frames < 300 * 60 for r in report.rounds), "it never reaches five minutes"


def test_the_neutral_baseline_holds_the_throttle_it_started_with():
    """The first version of this sent four zeros, which closes the throttle.

    That is not "do nothing" — it is an order — and the crash it produced would
    have been read as the environment being harsher than it is.
    """
    action = neutral_policy()(np.zeros(20))
    assert action[3] == pytest.approx(INITIAL_THROTTLE)
    assert list(action[:3]) == [0.0, 0.0, 0.0]


def test_a_report_counts_what_it_says_it_counts():
    report = evaluate(neutral_policy(), EnvConfig(), rounds=4, seed=100)
    won = sum(1 for r in report.rounds if r.won)
    assert report.win_rate == pytest.approx(won / 4)
    assert "rounds" in report.summary()
    assert report.as_dict()["rounds"] == 4
    assert len(report.as_dict()["detail"]) == 4
