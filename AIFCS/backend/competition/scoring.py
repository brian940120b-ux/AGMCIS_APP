"""The organiser's scoring, so we can measure ourselves with the judge's ruler.

    S           = S_advantage, or S_advantage + S_kill once three seconds of
                  tracking have accumulated
    S_kill      = W_base + (300 - T_kill)
    S_advantage = W_time * T_att - W_G * T_G + W_pos * sum(P_t)
    P(t)        = (TA_norm + AA_norm) * Distance_Factor

This exists because the reference training reward and the competition scoring
are not the same function, and only one of them decides who goes through. The
reward pays for pointing the nose at any range; the scoring pays for pointing
it between 500 and 3000 ft, and pays more for holding station than for any
single instant of aim. A policy tuned on the reward can be beaten by one tuned
on the score while looking better in training.

Three readings of the published rules are choices rather than transcription,
and each is a named parameter with the reasoning written down:

* **The attack cone is a full angle of 2 degrees, so the half-angle is 1.** The
  figure's arrow spans the cone, and the reference environment tests its own
  track angle — a half-angle by construction — against 1.0. If that reading is
  wrong the real cone is more generous, and a policy trained to the tighter one
  still scores; the opposite mistake would have us counting hits we never made.
* **sum(P_t) accumulates every frame, not every second.** With W_pos = 10 at
  60 Hz, a perfectly placed second is worth 1440 against 2000 for a second of
  tracking, which is a balance. Sampled once a second it would be worth 24, and
  a term worth 24 against 2000 would not have been given a weight, a table and
  a figure.
* **T_G counts seconds above 9G**, read from the pilot Z-axis load the OBS
  packet already carries.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum

from competition.state import Geometry

FT_PER_M = 1.0 / 0.3048

#: Distance factor, 表 4 of NCSIST-AIPilot-01. Upper bound of each band, in
#: metres, with the factor that applies below it. Note the bands are metres
#: while the attack envelope is feet: the rules use both.
DISTANCE_FACTOR_BANDS: tuple[tuple[float, float], ...] = (
    (150.0, 0.1),  # collision risk
    (500.0, 1.2),  # best attack
    (1500.0, 1.0),  # effective engagement
    (3000.0, 0.5),  # tactical
    (math.inf, 0.1),  # reconnaissance
)

#: Crash. The rules give it in metres; the reference environment agrees.
CRASH_ALTITUDE_M = 50.0
#: Mid-air collision.
COLLISION_DISTANCE_M = 15.0
#: Above this the G penalty accrues.
G_LIMIT = 9.0
#: One round.
ROUND_SECONDS = 300.0


def distance_factor(distance_m: float) -> float:
    """The weight the position score carries at this range."""
    for upper_m, factor in DISTANCE_FACTOR_BANDS:
        if distance_m < upper_m:
            return factor
    return DISTANCE_FACTOR_BANDS[-1][1]


@dataclass(frozen=True)
class ScoringWeights:
    """The reference values the organiser published. They may set others."""

    kill_base: float = 1000.0
    kill_time_budget_s: float = 300.0
    attack_time: float = 2000.0
    high_g: float = 1000.0
    position: float = 10.0


@dataclass(frozen=True)
class AttackEnvelope:
    """Where a shot counts, and for how long it has to count."""

    half_angle_deg: float = 1.0
    min_range_ft: float = 500.0
    max_range_ft: float = 3000.0
    kill_seconds: float = 3.0

    def contains(self, geometry: Geometry) -> bool:
        return (
            geometry.track_angle_deg <= self.half_angle_deg
            and self.min_range_ft <= geometry.distance_ft <= self.max_range_ft
        )


def position_advantage(geometry: Geometry) -> float:
    """P(t): how good this instant's geometry is, from 0 to 2.4.

    Two halves. Track angle asks whether our nose is on them; aspect angle asks
    whether we are behind them. Both are worth nothing beyond 90 degrees, so a
    head-on merge scores on neither and an overshoot scores on neither.
    """
    track = _normalised(geometry.track_angle_deg)
    aspect = _normalised(geometry.aspect_angle_deg)
    return (track + aspect) * distance_factor(geometry.distance_m)


def _normalised(angle_deg: float) -> float:
    magnitude = abs(angle_deg)
    if magnitude >= 90.0:
        return 0.0
    return (90.0 - magnitude) / 90.0


@dataclass
class SideScore:
    """One aircraft's accumulating score through a round.

    Fed a frame at a time so it can be run live, against a replay, or inside
    the training environment — which is the point of having it at all.
    """

    weights: ScoringWeights = field(default_factory=ScoringWeights)
    envelope: AttackEnvelope = field(default_factory=AttackEnvelope)
    tick_hz: float = 60.0

    attack_seconds: float = 0.0
    high_g_seconds: float = 0.0
    position_sum: float = 0.0
    elapsed_s: float = 0.0
    killed_at_s: float | None = None

    @property
    def _dt(self) -> float:
        return 1.0 / self.tick_hz

    def observe(self, geometry: Geometry, g_load: float) -> None:
        """One frame of the round."""
        self.elapsed_s += self._dt
        self.position_sum += position_advantage(geometry)
        if abs(g_load) > G_LIMIT:
            self.high_g_seconds += self._dt
        if self.envelope.contains(geometry):
            self.attack_seconds += self._dt
            if self.killed_at_s is None and self.attack_seconds >= self.envelope.kill_seconds - 1e-9:
                self.killed_at_s = self.elapsed_s
        # Not reset when the target leaves the cone: the rules accumulate
        # ("累積時間"), so three separate seconds of tracking are a kill.

    @property
    def killed(self) -> bool:
        return self.killed_at_s is not None

    @property
    def advantage_score(self) -> float:
        return (
            self.weights.attack_time * self.attack_seconds
            - self.weights.high_g * self.high_g_seconds
            + self.weights.position * self.position_sum
        )

    @property
    def kill_score(self) -> float:
        """Zero until three seconds have accumulated, then paid for speed."""
        if self.killed_at_s is None:
            return 0.0
        return self.weights.kill_base + (self.weights.kill_time_budget_s - self.killed_at_s)

    @property
    def total(self) -> float:
        return self.advantage_score + self.kill_score

    def as_dict(self) -> dict[str, float | bool | None]:
        return {
            "attack_seconds": round(self.attack_seconds, 3),
            "high_g_seconds": round(self.high_g_seconds, 3),
            "position_sum": round(self.position_sum, 2),
            "elapsed_s": round(self.elapsed_s, 3),
            "killed": self.killed,
            "killed_at_s": None if self.killed_at_s is None else round(self.killed_at_s, 3),
            "advantage_score": round(self.advantage_score, 1),
            "kill_score": round(self.kill_score, 1),
            "total": round(self.total, 1),
        }


class Verdict(StrEnum):
    BLUE = "BLUE"
    RED = "RED"
    #: Identical on both scores: the rules replay the round rather than call it.
    REPLAY = "REPLAY"


class EndReason(StrEnum):
    KILL = "KILL"
    SIMULTANEOUS_KILL = "SIMULTANEOUS_KILL"
    CRASH = "CRASH"
    COLLISION = "COLLISION"
    TIME = "TIME"


@dataclass(frozen=True)
class RoundOutcome:
    verdict: Verdict
    reason: EndReason
    blue: dict[str, float | bool | None]
    red: dict[str, float | bool | None]
    note: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "verdict": self.verdict.value,
            "reason": self.reason.value,
            "note": self.note,
            "blue": self.blue,
            "red": self.red,
        }


def decide_round(
    blue: SideScore,
    red: SideScore,
    *,
    blue_crashed: bool = False,
    red_crashed: bool = False,
    collided: bool = False,
) -> RoundOutcome:
    """Who won, by 表 3.

    Order matters and is the table's: a kill ends the round outright and beats
    any score ("無視當下任何分數"); a crash is decided by who is still flying;
    everything else falls through to the advantage score.
    """
    if blue.killed and red.killed:
        return _by_advantage(
            blue, red, EndReason.SIMULTANEOUS_KILL, "both reached three seconds in the same frame"
        )
    if blue.killed:
        return RoundOutcome(Verdict.BLUE, EndReason.KILL, blue.as_dict(), red.as_dict())
    if red.killed:
        return RoundOutcome(Verdict.RED, EndReason.KILL, blue.as_dict(), red.as_dict())

    if collided:
        return _by_advantage(blue, red, EndReason.COLLISION, "mid-air collision")
    if blue_crashed and red_crashed:
        return _by_advantage(blue, red, EndReason.CRASH, "both were lost")
    if blue_crashed:
        return RoundOutcome(Verdict.RED, EndReason.CRASH, blue.as_dict(), red.as_dict(), "blue was lost")
    if red_crashed:
        return RoundOutcome(Verdict.BLUE, EndReason.CRASH, blue.as_dict(), red.as_dict(), "red was lost")

    return _by_advantage(blue, red, EndReason.TIME, "five minutes elapsed")


def _by_advantage(blue: SideScore, red: SideScore, reason: EndReason, note: str) -> RoundOutcome:
    if blue.advantage_score > red.advantage_score:
        verdict = Verdict.BLUE
    elif red.advantage_score > blue.advantage_score:
        verdict = Verdict.RED
    else:
        verdict = Verdict.REPLAY
    return RoundOutcome(verdict, reason, blue.as_dict(), red.as_dict(), note)
