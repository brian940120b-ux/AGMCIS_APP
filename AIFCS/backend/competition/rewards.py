"""Two rewards, because there are two defensible answers and only one winner.

**reference** is a port of the package's `_compute_reward_and_done`. It is what
the 314M-step policy learned from, so it is the control in any comparison.

**score** is the per-frame increment of the competition's own advantage score,
plus terminal terms for the outcomes the rules end a round on. It optimises the
thing the judges add up.

They differ where it matters. The reference pays `(2 - track_angle)` for a nose
on target at any range whatsoever — it has no distance term — while the
competition counts only 500 to 3,000 ft and weights position by a distance
factor that peaks between 150 and 500 m and falls to a twelfth of that beyond
3 km. A policy can maximise the first from two miles out and score nothing.

Which is better is not decidable from reading them, so both are here and the
answer is an experiment.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from competition.scoring import (
    G_LIMIT,
    AttackEnvelope,
    ScoringWeights,
    position_advantage,
)
from competition.state import Geometry

FT_PER_M = 1.0 / 0.3048


class RewardMode(StrEnum):
    REFERENCE = "reference"
    SCORE = "score"


def sigmoid(x: float, rate: float, midpoint: float) -> float:
    """The reference's own numerically-stable sigmoid, kept branch for branch."""
    d = rate * (x - midpoint)
    if d >= 0:
        return 1.0 / (1.0 + math.exp(-d))
    return math.exp(d) / (1.0 + math.exp(d))


@dataclass
class ReferenceReward:
    """`_compute_reward_and_done`, ported.

    Three shaping terms and two terminal ones:

    * a deck penalty that fades in below about 1,300 ft;
    * a closing-rate reward whose weight is nearly zero inside 2,900 ft and
      full beyond it, so it pays for getting there rather than for arriving;
    * a tracking penalty of 0.01 per degree outside a one-degree deadzone;
    * `2 - track_angle` while inside that deadzone, at any range;
    * -10 and done on own crash, 0 and done on the opponent's — deliberately
      unrewarded, so a policy cannot learn to wait for the other side to fall
      out of the sky.
    """

    previous_distance_m: float | None = None

    def reset(self) -> None:
        self.previous_distance_m = None

    def __call__(self, geometry: Geometry, *, crashed: bool, foe_crashed: bool) -> float:
        if crashed:
            return -10.0
        if foe_crashed:
            return 0.0

        reward = 0.0
        altitude_ft = geometry.own_alt_m * FT_PER_M
        reward += -5.0 * (1.0 - sigmoid(altitude_ft, 1 / 20, 1300))

        if self.previous_distance_m is not None:
            closing_m = self.previous_distance_m - geometry.distance_m
            weight = sigmoid(geometry.distance_ft, 1 / 500, 2900)
            reward += max(-2.0, min(2.0, closing_m * 0.5)) * weight
        self.previous_distance_m = geometry.distance_m

        if geometry.track_angle_deg <= 1.0:
            reward += 2.0 - geometry.track_angle_deg

        reward -= max(0.0, abs(geometry.azimuth_deg) - 1.0) * 0.01
        reward -= max(0.0, abs(geometry.elevation_deg) - 1.0) * 0.01
        return reward


@dataclass
class ScoreReward:
    """What the judges add up, one frame at a time.

    The shaping is the per-frame contribution to `S_advantage`, which needs no
    invention: the scoring already defines a value for every instant. Divided
    by `scale` only to keep the numbers in the range value functions like —
    a frame is worth up to about 57 raw, and the division changes nothing about
    which policy is better.

    The terminal terms are the outcomes the rules end a round on, carried at
    their own weights so that a fast kill beats a slow one here for the same
    reason it does on the day.
    """

    weights: ScoringWeights
    envelope: AttackEnvelope
    tick_hz: float = 60.0
    scale: float = 10.0
    #: The reference's crash penalty, kept: -10 against a per-frame reward of a
    #: few units is a strong signal, and losing the aircraft loses the round.
    crash_penalty: float = -10.0

    def reset(self) -> None:  # no memory, but the interface is shared
        return None

    def __call__(
        self,
        geometry: Geometry,
        *,
        g_load: float,
        crashed: bool,
        foe_crashed: bool,
        killed_at_s: float | None = None,
    ) -> float:
        if crashed:
            return self.crash_penalty
        if foe_crashed:
            # Same reasoning as the reference: an opponent that falls out of the
            # sky is not an achievement, and paying for it teaches patience.
            return 0.0

        dt = 1.0 / self.tick_hz
        reward = self.weights.position * position_advantage(geometry)
        if self.envelope.contains(geometry):
            reward += self.weights.attack_time * dt
        if abs(g_load) > G_LIMIT:
            reward -= self.weights.high_g * dt

        if killed_at_s is not None:
            reward += self.weights.kill_base + (self.weights.kill_time_budget_s - killed_at_s)
        return reward / self.scale
