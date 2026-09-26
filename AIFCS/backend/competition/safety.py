"""A rule-based floor under the policy: do not fly into the ground.

The competition explicitly allows this. 公告說明 一.1.(2) lists the permitted
control techniques as "Rule-Base、行為樹、決策樹、貝葉斯、AI、機器學習、深度學習、
強化學習、LLM" — a rule-based layer and a learned policy are the same kind of
thing as far as the rules are concerned, and combining them needs no licence.

It earns its place on the numbers. A centred stick puts this aircraft into the
ground in 49 seconds: it is initialised without trim, the same way the
reference initialises it, so it pitches down and accelerates from 340 to 694
KCAS on the way. Every policy has to spend part of its budget learning not to
do that before any of it can go on fighting, and an undertrained one does not
get there — ours crashed in every round.

Losing the aircraft loses the round outright, whatever the score
(表 3: 單方失控/墜毀 → 存活方勝利). So the cheapest available point is simply
not to.

What this deliberately is not: a controller. It does not fly, it does not
manoeuvre, and it hands back what the policy asked for the moment the aircraft
is out of danger. It moves two channels, briefly, when the ground is close and
approaching. Everything else is the policy's.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from competition.state import Telemetry

FT_PER_M = 1.0 / 0.3048


@dataclass(frozen=True)
class GroundAvoidance:
    """Pull up when the ground is close and getting closer.

    The test is time-to-impact rather than altitude alone, because at 694 KCAS
    pointing down, 2,000 ft is a second and a half and 2,000 ft in level flight
    is not an emergency at all. A fixed altitude floor would either fire
    constantly up high or fire too late in a dive; a predicted impact time
    fires exactly when it has to.
    """

    #: Fire when the ground is this close in time, at the current descent rate.
    seconds_to_impact: float = 8.0
    #: Never fire above this, however steep the dive — there is room to recover.
    ceiling_ft: float = 8000.0
    #: Below this, fire regardless of descent rate.
    floor_ft: float = 500.0
    #: How hard to pull, as a magnitude. Applied **negative**, because in this
    #: model a positive elevator command puts the nose down: measured, +0.5
    #: from level takes the pitch to -14.7 degrees in three seconds and -0.5
    #: takes it to +4.7. The first version of this pulled the wrong way and
    #: made the aircraft hit the ground 160 frames sooner, which is how the
    #: sign was found.
    #:
    #: Not 1.0: the scoring charges 1000 a second above 9G, and a recovery that
    #: costs more than the crash it avoids is not one.
    elevator: float = 0.6
    #: Roll towards wings level, so the pull goes upwards rather than sideways.
    roll_gain: float = 0.02

    def danger(self, telemetry: Telemetry) -> bool:
        """Is the ground close enough, soon enough, to take the stick?"""
        altitude_ft = telemetry.own_alt_ft
        if altitude_ft <= self.floor_ft:
            return True
        if altitude_ft > self.ceiling_ft:
            return False
        descent_fps = telemetry.own_vd_fps  # positive is downwards
        if descent_fps <= 0.0:
            return False
        return altitude_ft / descent_fps < self.seconds_to_impact

    def __call__(self, raw_action: np.ndarray, telemetry: Telemetry) -> np.ndarray:
        """The policy's action, or a recovery if the ground demands one.

        Returned before the stick shaping rather than after, so a recovery is
        rate-limited exactly like any other command. Snapping the surfaces
        would trade a crash for a 9G penalty and possibly a departure.
        """
        if not self.danger(telemetry):
            return raw_action

        action = np.array(raw_action, dtype=np.float64)
        # Nose up, which is negative here. `min` so that a policy already
        # pulling harder than this is left alone.
        action[1] = min(float(action[1]), -self.elevator)
        # Wings level, proportional to how far off level we are. Rolling
        # upright first is what makes the pull lift rather than turn.
        action[0] = float(np.clip(-telemetry.own_roll_deg * self.roll_gain, -1.0, 1.0))
        # The throttle is left to the policy. Adding power in a dive arrives at
        # the ground sooner, and taking it away is a judgement about the fight
        # that this layer has no business making.
        return action
