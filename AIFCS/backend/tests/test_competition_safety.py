"""The rule-based floor under the policy (COMP PHASE 14).

Allowed outright: 公告說明 一.1.(2) lists Rule-Base alongside 強化學習 among the
permitted control techniques, so a hybrid needs no licence.

Worth having: a centred stick flies this aircraft into the ground in 49
seconds, and losing the aircraft loses the round whatever the score.
"""

from __future__ import annotations

import numpy as np
import pytest

from competition.safety import GroundAvoidance
from competition.state import Telemetry

pytest.importorskip("jsbsim")

from competition.environment import EnvConfig
from competition.evaluate import evaluate, neutral_policy


def flying(altitude_ft: float, descent_fps: float, roll_deg: float = 0.0) -> Telemetry:
    """Only the three fields the floor reads; the rest are placeholders."""
    values = dict.fromkeys(Telemetry.__dataclass_fields__, 0.0)
    values["own_alt_ft"] = altitude_ft
    values["own_vd_fps"] = descent_fps
    values["own_roll_deg"] = roll_deg
    return Telemetry(**values)


# --------------------------------------------------------------- when it fires


def test_it_stays_out_of_the_way_in_level_flight():
    floor = GroundAvoidance()
    assert not floor.danger(flying(altitude_ft=15_000, descent_fps=0.0))
    assert not floor.danger(flying(altitude_ft=2_000, descent_fps=0.0))
    assert not floor.danger(flying(altitude_ft=2_000, descent_fps=-300.0)), "climbing"


def test_it_fires_on_time_to_impact_not_altitude_alone():
    """The reason the test is a time and not a height.

    Two thousand feet in level flight is not an emergency. Two thousand feet
    pointing down at 600 ft/s is three seconds. An altitude floor would either
    fire constantly up high or fire far too late in a dive.
    """
    floor = GroundAvoidance(seconds_to_impact=8.0)
    assert floor.danger(flying(altitude_ft=2_000, descent_fps=600.0)), "3.3 s to impact"
    assert not floor.danger(flying(altitude_ft=2_000, descent_fps=100.0)), "20 s to impact"


def test_it_fires_near_the_ground_however_gently():
    floor = GroundAvoidance(floor_ft=500.0)
    assert floor.danger(flying(altitude_ft=400, descent_fps=1.0))


def test_it_does_nothing_above_the_ceiling():
    """High up there is room to recover, and taking the stick costs the fight."""
    floor = GroundAvoidance(ceiling_ft=8000.0)
    assert not floor.danger(flying(altitude_ft=9_000, descent_fps=5_000.0))


# ------------------------------------------------------------ what it commands


def test_it_pulls_the_nose_up_which_is_negative_here():
    """The sign that the first version had backwards.

    Measured in this environment: +0.5 elevator from level takes the pitch to
    -14.7 degrees in three seconds, -0.5 takes it to +4.7. Positive is nose
    down. Pulling the wrong way put the aircraft into the ground 160 frames
    sooner than doing nothing at all, which is how it was found.
    """
    floor = GroundAvoidance(elevator=0.6)
    action = floor(np.zeros(4), flying(altitude_ft=300, descent_fps=500.0))
    assert action[1] == pytest.approx(-0.6), "negative is nose up"


def test_it_leaves_a_policy_that_is_already_pulling_harder_alone():
    floor = GroundAvoidance(elevator=0.6)
    action = floor(np.array([0.0, -0.9, 0.0, 0.5]), flying(altitude_ft=300, descent_fps=500.0))
    assert action[1] == pytest.approx(-0.9)


def test_it_rolls_towards_wings_level():
    """A pull only lifts if the wings are level; otherwise it turns."""
    floor = GroundAvoidance(roll_gain=0.02)
    right = floor(np.zeros(4), flying(altitude_ft=300, descent_fps=500.0, roll_deg=60.0))
    left = floor(np.zeros(4), flying(altitude_ft=300, descent_fps=500.0, roll_deg=-60.0))
    assert right[0] < 0 < left[0], "roll back the way it came"


def test_it_does_not_touch_the_throttle():
    """Adding power in a dive arrives at the ground sooner, and taking it away
    is a judgement about the fight that a safety layer has no business making."""
    floor = GroundAvoidance()
    action = floor(np.array([0.0, 0.0, 0.0, 0.42]), flying(altitude_ft=300, descent_fps=500.0))
    assert action[3] == pytest.approx(0.42)


def test_it_returns_the_policys_action_untouched_when_there_is_no_danger():
    floor = GroundAvoidance()
    asked = np.array([0.1, 0.2, 0.3, 0.4])
    assert floor(asked, flying(altitude_ft=15_000, descent_fps=0.0)) is asked


# ---------------------------------------------------------------- end to end


def test_the_floor_turns_every_crash_into_a_full_round():
    """The measurement that decides whether it earns its place.

    A centred stick, which crashes in every round without it. With it, every
    round runs the full five minutes — and against the reference opponent that
    is enough to win most of them, because the opponent flies straight and a
    round that reaches time is decided on advantage score.
    """
    without = evaluate(neutral_policy(), EnvConfig(), rounds=3, seed=100)
    with_floor = evaluate(neutral_policy(), EnvConfig(ground_avoidance=GroundAvoidance()), rounds=3, seed=100)

    assert without.crash_rate == 1.0
    assert with_floor.crash_rate == 0.0
    assert all(r.frames == 18_000 for r in with_floor.rounds), "every round reaches five minutes"
    assert with_floor.mean_margin > without.mean_margin
