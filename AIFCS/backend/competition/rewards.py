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
from typing import Any

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
    #: The score, minus the opponent's. The rules decide a round that reaches
    #: time by 作戰優勢分高者勝 — whose advantage score is larger — so what
    #: settles it is the difference, and denying a point is worth scoring one.
    MARGIN = "margin"
    #: The margin plus shaping the score cannot provide, because the score is a
    #: measurement and not a teacher. See `ShapedReward`.
    SHAPED = "shaped"
    #: `shaped` with a second tracking term that is steep near the cone. A new
    #: name rather than a changed `shaped`, so v6 stays reproducible and the
    #: two are one measured difference apart. See `PointedReward`.
    POINTED = "pointed"
    #: The margin plus a *potential-based* version of `shaped`'s tracking
    #: term: paid for improving the geometry, not for sitting in it. Holding
    #: the cone is paid by the official attack term alone. See
    #: `PotentialReward`.
    POTENTIAL = "potential"


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

    #: Subtract the opponent's own advantage for this frame, making the reward
    #: the per-frame change in the score *margin*. Off by default so that
    #: `score` stays exactly the competition's own number for one side.
    defensive: bool = False

    def frame_advantage(self, geometry: Geometry, g_load: float) -> float:
        """One side's contribution to `S_advantage` for one frame, unscaled."""
        dt = 1.0 / self.tick_hz
        value = self.weights.position * position_advantage(geometry)
        if self.envelope.contains(geometry):
            value += self.weights.attack_time * dt
        if abs(g_load) > G_LIMIT:
            value -= self.weights.high_g * dt
        return value

    def __call__(
        self,
        geometry: Geometry,
        *,
        g_load: float,
        crashed: bool,
        foe_crashed: bool,
        killed_at_s: float | None = None,
        foe_geometry: Geometry | None = None,
        foe_g_load: float = 0.0,
        foe_killed_at_s: float | None = None,
    ) -> float:
        if crashed:
            return self.crash_penalty
        if foe_crashed:
            # Same reasoning as the reference: an opponent that falls out of the
            # sky is not an achievement, and paying for it teaches patience.
            return 0.0

        reward = self.frame_advantage(geometry, g_load)
        if killed_at_s is not None:
            reward += self.weights.kill_base + (self.weights.kill_time_budget_s - killed_at_s)

        if self.defensive and foe_geometry is not None:
            reward -= self.frame_advantage(foe_geometry, foe_g_load)
            if foe_killed_at_s is not None:
                reward -= self.weights.kill_base + (self.weights.kill_time_budget_s - foe_killed_at_s)
        return reward / self.scale


@dataclass
class ShapedReward:
    """The margin, plus the gradient the score does not provide.

    A score is a measurement, not a teacher. The competition's attack term is
    an indicator on a one-degree cone: worth 2000 a second inside it and
    nothing at all outside, with no slope in between. A policy that has never
    been inside it therefore gets no signal pointing that way — the needle is
    the reward, and finding needles is not what gradient ascent is for.

    The terms below are from the PHANG-MAN agent's reward function (Pope, Ide
    et al., DARPA AlphaDogfight Trials, arXiv 2105.00990, table I), which is
    also where the organiser's own reference reward came from: its deck
    penalty and its closure term carry that paper's constants — 1/20 and 1300,
    1/500 and 2900 — unchanged. What the organiser dropped, and what is put
    back here, is everything that gives the geometry a slope.

    * **tracking** — a soft version of the attack condition, so pointing more
      nearly at the target is worth more than pointing less nearly, at every
      angle rather than only inside one degree.
    * **range window** — the paper's `Gamma_B(d)`: the tracking bonus is worth
      most inside the firing envelope and fades outside it, so closing to a
      range where a shot counts is itself rewarded.
    * **too close** — inside 150 m the score's distance factor collapses to a
      twelfth and a collision ends the round for both. Overshooting is a
      mistake the score punishes only indirectly.
    * **deck** — the reference's own term, kept, because a policy that flies
      into the ground scores nothing and ours does exactly that.

    None of it is free: shaping that does not point at the score is a way to
    lose while looking good, which is why `margin` exists unshaped as the
    control in any comparison.
    """

    weights: ScoringWeights
    envelope: AttackEnvelope
    tick_hz: float = 60.0
    scale: float = 10.0
    crash_penalty: float = -10.0
    #: Weight on the soft tracking bonus, in the same units as the score.
    tracking: float = 400.0
    #: Weight on the second, steep tracking term. Zero in `shaped`, which is
    #: what v6 trained under; `pointed` turns it on.
    fine_tracking: float = 0.0
    #: Where the steep term is half paid, in degrees of track angle, and how
    #: quickly it falls away either side. 2.0 and 0.7 put nearly all of its
    #: gradient between five degrees and half a degree, which is the span the
    #: linear term cannot see.
    fine_midpoint_deg: float = 2.0
    fine_scale_deg: float = 0.7
    #: Weight on the overshoot penalty.
    too_close: float = 200.0
    #: Weight on the deck penalty, the reference's own value.
    deck: float = 5.0

    def __post_init__(self) -> None:
        self._score = ScoreReward(
            weights=self.weights,
            envelope=self.envelope,
            tick_hz=self.tick_hz,
            scale=1.0,  # scaled once, at the end
            crash_penalty=self.crash_penalty,
            defensive=True,
        )

    def reset(self) -> None:
        self._score.reset()

    def range_factor(self, distance_ft: float) -> float:
        """`Gamma_B(d)`: how much a shot at this range is worth pursuing.

        One inside the envelope, falling away outside it. The paper uses a pair
        of sigmoids around a midpoint; this uses the envelope the rules
        actually publish, because we have that and the paper's agent did not.
        """
        low, high = self.envelope.min_range_ft, self.envelope.max_range_ft
        if low <= distance_ft <= high:
            return 1.0
        if distance_ft < low:
            # Closing inside the minimum is going the wrong way.
            return max(0.0, distance_ft / low) ** 2
        return float(sigmoid(distance_ft, -1.0 / 500.0, high + 900.0))

    def __call__(
        self,
        geometry: Geometry,
        *,
        g_load: float,
        crashed: bool,
        foe_crashed: bool,
        killed_at_s: float | None = None,
        foe_geometry: Geometry | None = None,
        foe_g_load: float = 0.0,
        foe_killed_at_s: float | None = None,
    ) -> float:
        if crashed:
            return self.crash_penalty
        if foe_crashed:
            return 0.0

        reward = self._score(
            geometry,
            g_load=g_load,
            crashed=False,
            foe_crashed=False,
            killed_at_s=killed_at_s,
            foe_geometry=foe_geometry,
            foe_g_load=foe_g_load,
            foe_killed_at_s=foe_killed_at_s,
        )

        dt = 1.0 / self.tick_hz
        window = self.range_factor(geometry.distance_ft)
        # A slope towards the cone, worth most where a shot would count.
        aim = max(0.0, 1.0 - geometry.track_angle_deg / 180.0)
        reward += self.tracking * dt * aim * window

        # The same idea again, but steep, and only near the cone. The linear
        # term above pays +0.278 for closing from 90 degrees to 40 and +0.022
        # for the whole of five degrees down to one — so the coarse turn is
        # worth more than the shot, and there is next to no gradient left over
        # the span that decides whether anything scores. Measured on v6: the
        # aim reaches 0.0 degrees, drifts at only 3.2 deg/s so the control has
        # room to hold it, and still spends just 3.8% of its close time inside
        # one degree, which is what the solid angle would give by chance
        # alone. It passes through because nothing pays it to stay.
        #
        # PHANG-MAN (arXiv 2105.00990) has two tracking terms for this reason:
        # a linear one for the turn and a logistic of steepness 1e5 on the
        # cone itself. Theirs is a step; this is a logistic with a real width,
        # because a step hands back the same flat gradient one degree further
        # out.
        if self.fine_tracking:
            fine = sigmoid(geometry.track_angle_deg, -1.0 / self.fine_scale_deg, self.fine_midpoint_deg)
            reward += self.fine_tracking * dt * fine * window

        # Overshoot. Below the envelope's minimum the score collapses anyway,
        # and a collision ends the round for both sides.
        if geometry.distance_m < 150.0:
            reward -= self.too_close * dt * (1.0 - geometry.distance_m / 150.0)

        # The deck, from the reference's own reward, in its own shape.
        altitude_ft = geometry.own_alt_m * FT_PER_M
        reward -= self.deck * (1.0 - sigmoid(altitude_ft, 1 / 20, 1300))

        return reward / self.scale


@dataclass
class PotentialReward:
    """The margin, plus shaping that cannot be farmed by looking good.

    `shaped` pays its tracking term every frame the nose is near the target,
    so a policy that drifts through the cone six times collects six times —
    and the measurement on v6 (docs/TUNING.md) says that is what it learned:
    time inside one degree no better than the solid angle gives by chance.

    Potential-based shaping (Ng, Harada & Russell 1999) pays only the *change*
    in a potential, `gamma * Phi(s') - Phi(s)`, which telescopes over a round
    to `Phi(end) - Phi(start)`: the sum a policy can collect is bounded by
    how much better the geometry is at the end than at the start, however
    many times it passes through the middle. Staying in the cone earns
    nothing from the shaping — and 2,000 a second from the official attack
    term, which is the money that should be doing the teaching. Both teams
    that published a working recipe on this geometry shaped this way
    (SRC-012's `_shaping_potential` over range and both track angles, SRC-013's
    `gamma*Phi(s') - Phi(s)`), and LAG (SRC-015) marks its posture reward
    `potential: true`.

    Phi is `shaped`'s own tracking bonus turned into a state value —
    `potential * aim * range_factor`, ours minus the opponent's — so the two
    modes differ by exactly one thing: whether the bonus is paid per frame
    or as a difference. The deck term is kept as it is in `shaped`, per
    frame, because it is a safety cost rather than a goal.

    `gamma` should be the agent's discount for the invariance result to hold
    exactly; 1.0 is the plain difference and is close enough at 0.995 that
    the mismatch is the size of the term itself, one frame in two hundred.
    """

    weights: ScoringWeights
    envelope: AttackEnvelope
    tick_hz: float = 60.0
    scale: float = 10.0
    crash_penalty: float = -10.0
    #: Phi's full-scale value, in score units. 2,000 is one second of the
    #: official attack term: the whole shaping a round can yield, far and
    #: unaimed to in-cone and in-range, is worth about one second of holding
    #: the cone. SRC-012 sized theirs the same way ("about one hit").
    potential: float = 2000.0
    gamma: float = 1.0
    deck: float = 5.0

    def __post_init__(self) -> None:
        # Everything but the tracking term, which `shaped` pays per frame and
        # this pays as a difference. tracking=0 leaves margin + deck.
        self._base = ShapedReward(
            weights=self.weights,
            envelope=self.envelope,
            tick_hz=self.tick_hz,
            scale=1.0,
            crash_penalty=self.crash_penalty,
            tracking=0.0,
            fine_tracking=0.0,
            too_close=0.0,
            deck=self.deck,
        )
        self._previous: float | None = None

    def reset(self) -> None:
        self._base.reset()
        self._previous = None

    def phi(self, geometry: Geometry, foe_geometry: Geometry | None = None) -> float:
        """The state's value under the shaping: aim times range worth, ours minus theirs."""
        own = max(0.0, 1.0 - geometry.track_angle_deg / 180.0) * self._base.range_factor(geometry.distance_ft)
        value = own
        if foe_geometry is not None:
            foe = max(0.0, 1.0 - foe_geometry.track_angle_deg / 180.0) * self._base.range_factor(
                foe_geometry.distance_ft
            )
            value -= foe
        return self.potential * value

    def __call__(
        self,
        geometry: Geometry,
        *,
        g_load: float,
        crashed: bool,
        foe_crashed: bool,
        killed_at_s: float | None = None,
        foe_geometry: Geometry | None = None,
        foe_g_load: float = 0.0,
        foe_killed_at_s: float | None = None,
    ) -> float:
        if crashed:
            return self.crash_penalty
        if foe_crashed:
            return 0.0

        reward = self._base(
            geometry,
            g_load=g_load,
            crashed=False,
            foe_crashed=False,
            killed_at_s=killed_at_s,
            foe_geometry=foe_geometry,
            foe_g_load=foe_g_load,
            foe_killed_at_s=foe_killed_at_s,
        )

        current = self.phi(geometry, foe_geometry)
        # First frame of a round: nothing to differ from, and no invented
        # difference against the last round's final geometry.
        if self._previous is not None:
            reward += self.gamma * current - self._previous
        self._previous = current
        return reward / self.scale


def PointedReward(**kwargs: Any) -> ShapedReward:
    """`shaped` with the steep near-cone term switched on.

    A separate name, not a changed `shaped`, for two reasons. v6 is the only
    thing on the board that is any good and it has to stay reproducible; and
    the point of the run is to attribute a change, which needs the two to
    differ by one thing that was written down.
    """
    kwargs.setdefault("fine_tracking", 400.0)
    return ShapedReward(**kwargs)
