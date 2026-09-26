"""Who we train against (COMP PHASE 15).

The reference opponent is a target drone — its heading logic chooses from
`["Straight"]` because the turn branches are commented out in the source — and
measured over five minutes it simply leaves: 45 km away and still going. A win
rate against that stops being informative long before it stops rising, and the
opponent on the day is another team's agent.

These test the controller's *behaviour*, not a win rate against it. Whether a
pursuing opponent trains a better policy is a question for a training run on a
GPU; whether it turns towards us is a question with an answer here.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from competition.state import Telemetry

pytest.importorskip("jsbsim")

from competition.environment import CompetitionRound, EnvConfig, pursuit_opponent
from competition.safety import GroundAvoidance


def seeing(
    azimuth_deg: float,
    distance_m: float = 2000.0,
    roll_deg: float = 0.0,
    height_above_ft: float = 0.0,
) -> Telemetry:
    """Telemetry for an opponent with us at a given bearing off its nose.

    Built by placing us on a flat tangent plane at that bearing, because the
    opponent reads the same geometry the policy does and a hand-written
    azimuth would be testing the test.
    """
    values = dict.fromkeys(Telemetry.__dataclass_fields__, 0.0)
    values["own_lat_deg"] = 23.0
    values["own_lon_deg"] = 121.0
    values["own_alt_ft"] = 15_000.0
    values["own_roll_deg"] = roll_deg
    values["own_vc_fps"] = 574.0  # about 340 KCAS
    values["own_vt_fps"] = 574.0

    bearing = math.radians(azimuth_deg)
    north, east = distance_m * math.cos(bearing), distance_m * math.sin(bearing)
    values["enemy_lat_deg"] = 23.0 + math.degrees(north / 6378137.0)
    values["enemy_lon_deg"] = 121.0 + math.degrees(east / (6378137.0 * math.cos(math.radians(23.0))))
    values["enemy_alt_ft"] = 15_000.0 + height_above_ft
    return Telemetry(**values)


def test_it_rolls_towards_the_side_the_target_is_on():
    """The first thing a turn towards someone is."""
    fly = pursuit_opponent(340.0)
    right = fly(seeing(+45.0))
    fly = pursuit_opponent(340.0)
    left = fly(seeing(-45.0))

    assert right[0] > 0, "target to the right, roll right"
    assert left[0] < 0, "target to the left, roll left"
    assert right[0] == pytest.approx(-left[0], rel=1e-6), "and symmetrically"


def test_it_does_not_roll_when_the_target_is_already_on_the_nose():
    fly = pursuit_opponent(340.0)
    assert fly(seeing(0.0))[0] == pytest.approx(0.0, abs=1e-6)


def test_it_keeps_the_wings_level_near_the_ground():
    """Rolling into a turn is how an aircraft arrives at the ground. Below the
    floor the fight loses to staying airborne."""
    fly = pursuit_opponent(340.0, floor_ft=3000.0)
    low = seeing(+60.0)
    low = Telemetry(**{**low.__dict__, "own_alt_ft": 1500.0})
    assert fly(low)[0] == pytest.approx(0.0, abs=1e-3)


def test_it_runs_the_engine_up_when_it_is_chasing():
    """Full power towards a target it is pointing at and not yet close to."""
    fly = pursuit_opponent(340.0)
    assert fly(seeing(10.0, distance_m=2000.0))[3] == pytest.approx(1.0)


def test_aggression_changes_how_hard_it_pulls():
    """One parameter, so a ladder of opponents is a curriculum.

    Measured at a settled PID rather than on the first frame: the derivative
    term sees the whole error arrive at once on frame one and saturates the
    elevator, which is a port artifact of the reference's own controller and
    hid this knob's effect entirely when the test read that frame.

    The target has to be above, too — `aggression` scales a vertical command,
    and with the opponent co-altitude and the wings level there is nothing for
    it to scale. A first version tested exactly that case and read "no effect"
    as a bug in the knob rather than in the scenario.
    """
    target = {"azimuth_deg": 20.0, "distance_m": 3000.0, "height_above_ft": 3000.0}
    settled = {}
    for aggression in (0.3, 2.0):
        fly = pursuit_opponent(340.0, aggression=aggression)
        for _ in range(30):
            action = fly(seeing(**target))
        settled[aggression] = action[1]

    assert settled[2.0] < settled[0.3], "negative elevator is nose up, so harder is lower"


def test_over_a_round_it_brings_us_onto_its_nose():
    """The reference opponent's azimuth to us barely moves and it ends 45 km
    away. This one converges towards zero, which is what pursuing is."""
    game = CompetitionRound(
        config=EnvConfig(opponent="pursuit", ground_avoidance=GroundAvoidance()), seed=100
    )
    game.reset(seed=100)
    hold = np.array([0.0, 0.0, 0.0, 0.8])

    bearings = []
    for frame in range(1, 9001):
        game.step(hold)
        if frame % 1800 == 0:
            bearings.append(abs(game.encoder.geometry(game.foe.telemetry_against(game.own)).azimuth_deg))

    assert bearings[-1] < bearings[0] / 2, f"should be turning to face us: {bearings}"
    assert bearings[-1] < 30.0
