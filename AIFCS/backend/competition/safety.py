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
    #:
    #: 8.0 was not enough. In a 60-degree dive from 10,000 ft the floor fired
    #: with 8.4 seconds of altitude left and still hit the ground, because the
    #: recovery itself takes longer than that at high Mach. Going to 12.0 takes
    #: the dive grid below from 4 crashes in 48 to 2, and costs 5.4 percentage
    #: points of stick time (14.9% to 20.3% of frames, measured against a
    #: random-walk policy that actually descends).
    seconds_to_impact: float = 12.0
    #: Never fire above this, however steep the dive — there is room to recover.
    #:
    #: 8,000 blocked the fire at 8,078 ft in the trace that found all of this,
    #: costing three and a half seconds of the recovery. Raising it to 15,000
    #: costs 0.3 percentage points of stick time; raising it further costs
    #: nothing at all and buys nothing either, because nothing in the measured
    #: rounds ever descends fast enough above 15,000 ft to trigger.
    ceiling_ft: float = 15000.0
    #: Below this, fire regardless of descent rate.
    floor_ft: float = 500.0
    #: How hard to pull, as a magnitude. Applied **negative**, because in this
    #: model a positive elevator command puts the nose down: measured, +0.5
    #: from level takes the pitch to -14.7 degrees in three seconds and -0.5
    #: takes it to +4.7. The first version of this pulled the wrong way and
    #: made the aircraft hit the ground 160 frames sooner, which is how the
    #: sign was found.
    #:
    #: **This is a stick command, not a surface deflection**, and the two are
    #: not close. `shape_command` cubes the elevator axis (`EXPONENTS[1] = 3`),
    #: so the 0.6 this used to ask for arrived as 0.216 — the floor was pulling
    #: at a third of what it thought. The trace: eight and a half seconds of
    #: asking for -0.60 and getting -0.22, a peak of 3.4G against a 9G budget,
    #: and the ground.
    #:
    #: 1.0 does not mean 1.0 at the surface either. Above Mach 0.8 the shaping
    #: caps the elevator at 0.4, and a dive is above Mach 0.8 within seconds.
    #: 1.0 means "everything the shaping will give us", which in an emergency
    #: is the right request: the measured peak is 7.9G, under the limit, and
    #: across 48 entry attitudes only one case exceeds 9G at all — a 2,500 ft
    #: entry at 85 degrees that was unrecoverable anyway, and whose 63G reading
    #: is the ground reaction at 3 ft, not the pull.
    #:
    #: The earlier note here said 1.0 was avoided because the scoring charges
    #: 1000 a second above 9G. That was reasoning about a cost instead of
    #: measuring it, and the cost is 150 points across the whole grid.
    elevator: float = 1.0
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
