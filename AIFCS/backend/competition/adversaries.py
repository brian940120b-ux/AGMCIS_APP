"""A fixed set of scripted opponents: the curriculum's low rungs, and the ruler.

Written because the measurement said our opponents were the weak link, not the
algorithm. A centred stick beats v5 eighty per cent of the time — so the pool
that v6 and v7p trained against, and the bar every generation was scored
against, contained a member that cannot beat doing nothing. Every claim of the
form "this generation is better than the last" rested on that.

Everyone who has done well at this uses many opponents, and most of them
scripted:

* PHANG-MAN (arXiv 2105.00990, AlphaDogfight runner-up) sampled from **thirty**,
  starting with scripted ones only and admitting learned ones once the win rate
  passed half.
* BVR Sim (arXiv 2608.25419) ships six — straight-line, random, aggressive,
  tactical, standoff, mutual-destruction — and names their three jobs:
  "curriculum opponents, reproducible evaluation references, and expert actions
  without requiring a separately trained checkpoint". We had none of the second.
* AlphaStar keeps main agents *plus* exploiters, for the same reason.

A scripted opponent does not drift. That is the point of the second job: a
learned opponent is a ruler whose marks move every time it is retrained, and
ours had a broken mark on it.

These are deliberately *tactics*, not optimised agents. Each one is a different
way of being hard to shoot, taken from the standard repertoire (Shaw, *Fighter
Combat*): a break turn denies the tracking solution by never letting the
geometry settle, an energy fighter refuses the turning fight altogether, a
scissors makes the attacker overshoot. A policy that beats all of them has had
to solve more than one problem, which is the nearest thing we can build to the
unknown agent it meets on the day.

Every one of them flies through `Telemetry` and returns the same four raw
control-surface commands as the reference's own opponent — aileron, elevator,
rudder, throttle, and **not** through the stick shaping, whose elevator curve is
a cube that would shrink these loops' corrections to a thousandth and fly the
aircraft into the ground. That is not hypothetical; it happened at 21 seconds.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from competition.state import StateEncoder, Telemetry

#: 60 Hz, the rate the rules run the world at.
SIM_HZ = 60.0

#: Below this, staying airborne outranks the fight. Every opponent here honours
#: it, because a scripted opponent that flies itself into the ground is not a
#: curriculum rung, it is a free win — which is exactly what v4 was to v5.
FLOOR_FT = 3000.0


def _surfaces(aileron: float, elevator: float, rudder: float, throttle: float) -> np.ndarray:
    return np.array(
        [
            float(np.clip(aileron, -1.0, 1.0)),
            float(np.clip(elevator, -1.0, 1.0)),
            float(np.clip(rudder, -1.0, 1.0)),
            float(np.clip(throttle, 0.0, 1.0)),
        ]
    )


class _Airframe:
    """The roll-and-pitch inner loop every one of these shares.

    Lifted from `pursuit_opponent`, which had it inline. Shared because six
    copies of a PID is six places for a sign to be wrong, and the elevator's
    sign has already been wrong twice in this project.
    """

    def __init__(self, floor_ft: float = FLOOR_FT) -> None:
        self.floor_ft = floor_ft
        self._roll_error = 0.0
        self._pitch_error = 0.0
        self._speed_error = 0.0

    def reset(self) -> None:
        self._roll_error = 0.0
        self._pitch_error = 0.0
        self._speed_error = 0.0

    def fly_to(
        self,
        telemetry: Telemetry,
        *,
        roll_deg: float,
        pitch_deg: float,
        throttle: float | None = None,
        speed_kcas: float = 340.0,
    ) -> np.ndarray:
        """Command an attitude, and get back the surfaces that hold it."""
        dt = 1.0 / SIM_HZ
        low = telemetry.own_alt_ft < self.floor_ft
        if low:
            # Wings level and nose up takes priority over any tactic. An
            # opponent that hits the ground teaches a policy nothing except
            # that waiting works.
            roll_deg = 0.0
            pitch_deg = max(pitch_deg, 5.0)

        roll_deg = float(np.clip(roll_deg, -80.0, 80.0))
        pitch_deg = float(np.clip(pitch_deg, -20.0, 25.0))

        roll_error = roll_deg - telemetry.own_roll_deg
        roll_rate = (roll_error - self._roll_error) / dt
        aileron = 0.02 * roll_error + 0.01 * roll_rate
        self._roll_error = roll_error

        pitch_error = pitch_deg - telemetry.own_pitch_deg
        pitch_rate = (pitch_error - self._pitch_error) / dt
        # Negative elevator is nose-up in the organiser's convention. Measured,
        # not assumed: n-pilot-z-norm reads -4.24 in a 4G pull.
        elevator = -(0.05 * pitch_error + 0.02 * pitch_rate)
        # The untrimmed aircraft's standing back pressure, plus what a bank
        # costs in lift.
        bank = abs(math.sin(math.radians(telemetry.own_roll_deg)))
        elevator -= 0.05 + bank * 0.2
        self._pitch_error = pitch_error

        if throttle is None:
            speed_error = speed_kcas - telemetry.own_vc_fps / 1.68781
            speed_rate = (speed_error - self._speed_error) / dt
            throttle = 0.5 + 0.1 * speed_error + 0.01 * speed_rate
            self._speed_error = speed_error
        else:
            self._speed_error = 0.0

        return _surfaces(aileron, elevator, 0.0, throttle)


class _Scripted:
    """An opponent with per-round state, reset the way a policy opponent is.

    `_build_opponent` calls `reset()` on a policy and rebuilds a scripted one
    from scratch each round. Giving these the same method means they can be
    used either way round without a caller having to know which it holds.
    """

    name = "scripted"

    def __init__(self, speed_kcas: float = 340.0, floor_ft: float = FLOOR_FT) -> None:
        self.speed_kcas = speed_kcas
        self.airframe = _Airframe(floor_ft)
        self.frame = 0
        self.encoder = StateEncoder()

    def reset(self) -> None:
        self.airframe.reset()
        self.frame = 0

    def __call__(self, telemetry: Telemetry) -> np.ndarray:
        self.frame += 1
        return self.act(telemetry)

    def act(self, telemetry: Telemetry) -> np.ndarray:  # pragma: no cover - abstract
        raise NotImplementedError


class BreakTurn(_Scripted):
    """A hard level turn, held. The hardest thing here to get a gun on.

    The classic gun defence: pull the tightest turn the aircraft has and keep
    pulling, so the attacker has to match the turn rate to stay in plane and
    the tracking solution never settles. Our own measurement says why this is
    the right rung to add — v6 reaches the cone and leaves it again, spending
    3.8% of its close time inside one degree when the solid angle alone would
    give 4%. Against something that never stops turning, that fraction is the
    whole problem, stated.

    Turns away from the threat rather than a fixed direction, so it cannot be
    beaten by learning which way it always goes.
    """

    name = "break"

    def act(self, telemetry: Telemetry) -> np.ndarray:
        geometry = self.encoder.geometry(telemetry)
        # Away from where they are: if they are off the left, roll right.
        direction = -1.0 if geometry.azimuth_deg > 0.0 else 1.0
        return self.airframe.fly_to(
            telemetry,
            roll_deg=direction * 75.0,
            # Nose up into the turn. A level break bleeds into the ground.
            pitch_deg=8.0,
            throttle=1.0,
        )


class EnergyFighter(_Scripted):
    """Refuses the turning fight: climbs, extends, comes back with height.

    The other half of the repertoire. A policy trained only against opponents
    that turn learns to win turning fights, and meets something on the day that
    simply leaves. Its answer to a threat is to unload and run, trade speed for
    height once clear, and re-engage from above.
    """

    name = "energy"

    def act(self, telemetry: Telemetry) -> np.ndarray:
        geometry = self.encoder.geometry(telemetry)
        threatened = geometry.distance_m < 2500.0 and abs(geometry.aspect_angle_deg) < 90.0

        if threatened:
            # Away from them, wings near level, everything into speed.
            away = geometry.azimuth_deg + 180.0
            if away > 180.0:
                away -= 360.0
            return self.airframe.fly_to(telemetry, roll_deg=away * 0.8, pitch_deg=-2.0, throttle=1.0)
        if telemetry.own_alt_ft < 12000.0:
            # Clear of them: buy height with the speed just gained.
            return self.airframe.fly_to(telemetry, roll_deg=0.0, pitch_deg=18.0, throttle=1.0)
        # High and fast: come back down on them.
        return self.airframe.fly_to(
            telemetry, roll_deg=geometry.azimuth_deg * 1.2, pitch_deg=-8.0, throttle=1.0
        )


class Scissors(_Scripted):
    """Reverses the turn on a fixed beat, to make an attacker overshoot.

    A rolling scissors is what a defender does when it cannot out-turn: each
    reversal forces the attacker either to overshoot in front — losing the
    position — or to bleed off speed matching it. Against a tracking policy it
    is specifically an attack on *holding* the cone rather than on entering it,
    which is the part v6 cannot do.

    The beat is fixed and known, which makes it learnable, and that is on
    purpose: it is a rung, not a boss.
    """

    name = "scissors"

    def __init__(self, *args: Any, period_s: float = 4.0, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.period_frames = max(1, int(period_s * SIM_HZ))

    def act(self, telemetry: Telemetry) -> np.ndarray:
        half = (self.frame // self.period_frames) % 2
        direction = 1.0 if half == 0 else -1.0
        return self.airframe.fly_to(telemetry, roll_deg=direction * 70.0, pitch_deg=10.0, throttle=0.9)


class Wanderer(_Scripted):
    """Smoothly random attitudes. Not a tactic — a test of robustness.

    Everything else here is a strategy a policy can learn to counter. This one
    exists to be uncorrelated with all of them, because the agent on the day
    was written by somebody else and will do something none of these do. A
    policy that only beats the five tactics above has been fitted to five
    tactics.

    Smooth rather than per-frame random: white noise on the surfaces averages
    to straight and level and would be a weaker opponent than the drone.
    """

    name = "wanderer"

    def __init__(self, *args: Any, seed: int = 0, hold_s: float = 3.0, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.seed = seed
        self.hold_frames = max(1, int(hold_s * SIM_HZ))
        self._random = np.random.default_rng(seed)
        self._roll = 0.0
        self._pitch = 0.0

    def reset(self) -> None:
        super().reset()
        # Reseeded per round, so a seeded evaluation is reproducible.
        self._random = np.random.default_rng(self.seed)
        self._roll = 0.0
        self._pitch = 0.0

    def act(self, telemetry: Telemetry) -> np.ndarray:
        if self.frame % self.hold_frames == 1:
            self._roll = float(self._random.uniform(-70.0, 70.0))
            self._pitch = float(self._random.uniform(-10.0, 15.0))
        return self.airframe.fly_to(
            telemetry,
            roll_deg=self._roll,
            pitch_deg=self._pitch,
            speed_kcas=self.speed_kcas,
        )


# ------------------------------------------------------------ doctrine set
#
# Four more, each one a page of the USAF F-16 handbook (AFTTP 3-3 Vol 5,
# SRC-022; docs/TACTICS.md) or of APL's AlphaDogfight adversaries (SRC-019).
# They differ from the first four in one way that matters for training: they
# *react to what the attacker is doing* — where its nose is, how close it is,
# whether it has just overshot — rather than flying a pattern. A policy that
# beats a break turn has learned to out-turn; a policy that beats these has
# learned to read the other aircraft.
#
# They are kept out of ADVERSARIES on purpose. ADVERSARIES is the scoreboard's
# ruler and a ruler does not grow marks; every board since EXP-001 is on those
# four. These are available to the pool and to evaluation by name.


def _away(azimuth_deg: float) -> float:
    """The roll that points the lift vector away from a bearing."""
    away = azimuth_deg + 180.0
    while away > 180.0:
        away -= 360.0
    while away < -180.0:
        away += 360.0
    return away


class Flare(_Scripted):
    """Cruises until something closes from behind, then throws out the anchor.

    APL's BUD FSM did exactly this (SRC-019): detect an opponent closing in from
    behind, select "flare", reduce speed hard to force the overshoot. The
    handbook's defensive chapter is built on the same bet: the attacker's
    closure is the defender's weapon (4.3.10.5.7). Here: throttle to idle, nose
    up to bleed speed, bank away; once the attacker is no longer behind,
    reverse into it to take the position it just gave up.

    Learnable, like everything here: a policy that controls its closure to the
    handbook's 5% rule never overshoots and the flare does nothing.
    """

    name = "flare"

    def __init__(self, *args: Any, trigger_m: float = 1200.0, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.trigger_m = trigger_m
        self._flaring_until = 0
        self._side = 1.0

    def reset(self) -> None:
        super().reset()
        self._flaring_until = 0
        self._side = 1.0

    def act(self, telemetry: Telemetry) -> np.ndarray:
        geometry = self.encoder.geometry(telemetry)
        # Aspect is the angle between their nose and the line from us to them,
        # measured as the encoder measures it: near 0 they are on our tail.
        behind = abs(geometry.azimuth_deg) > 120.0
        close = geometry.distance_m < self.trigger_m
        if behind and close and self.frame >= self._flaring_until:
            self._flaring_until = self.frame + int(3.0 * SIM_HZ)
            self._side = 1.0 if geometry.azimuth_deg > 0.0 else -1.0
        if self.frame < self._flaring_until:
            # Anchor out: idle, nose up, bank toward the side they are on so
            # the overshoot carries them past the other side.
            return self.airframe.fly_to(telemetry, roll_deg=self._side * 60.0, pitch_deg=15.0, throttle=0.0)
        if close and not behind:
            # They went past: turn into them.
            return self.airframe.fly_to(
                telemetry, roll_deg=geometry.azimuth_deg * 1.2, pitch_deg=4.0, throttle=1.0
            )
        return self.airframe.fly_to(telemetry, roll_deg=0.0, pitch_deg=2.0, speed_kcas=self.speed_kcas)


class Jinker(_Scripted):
    """Guns defence on the handbook's timing: out of plane when the shot is coming.

    AFTTP 3-3 4.3.10.4.3.2: begin the roll when the attacker pulls to lead, or
    at 3,000-4,000 ft slant range if the lead cue cannot be read; lift vector
    45-60 degrees off the attacker, pull *down* for one to two seconds, then
    back up below it. Timing is the whole trick: too early and the attacker
    re-solves the plane, too late and it has already fired. The cue used here
    is the attacker's nose inside ten degrees of us at under 1,200 m, which is
    "pulling to lead" as the OBS packet can see it. Between jinks it flies a
    gentle turn away, the sanctuary the handbook names: in tight and off the
    nose.

    What it is for: a policy that can hold the cone on a target that turns
    (BreakTurn) still has to learn that a target can *leave the plane* at the
    moment that matters. That is the step from 0.72 s in the cone to three.
    """

    name = "jinker"

    def __init__(self, *args: Any, trigger_m: float = 1200.0, lead_deg: float = 10.0, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.trigger_m = trigger_m
        self.lead_deg = lead_deg
        self._jink_until = 0
        self._cooldown_until = 0
        self._side = 1.0

    def reset(self) -> None:
        super().reset()
        self._jink_until = 0
        self._cooldown_until = 0
        self._side = 1.0

    def act(self, telemetry: Telemetry) -> np.ndarray:
        geometry = self.encoder.geometry(telemetry)
        # `aspect_angle_deg` is the reference's number: 0 when the other
        # aircraft flies straight at us, 180 when straight away. So it *is*
        # how far their nose is off us.
        their_nose_off_us = abs(geometry.aspect_angle_deg)
        shot_coming = geometry.distance_m < self.trigger_m and their_nose_off_us < self.lead_deg
        if shot_coming and self.frame >= self._cooldown_until:
            self._jink_until = self.frame + int(1.5 * SIM_HZ)
            self._cooldown_until = self._jink_until + int(2.0 * SIM_HZ)
            self._side = -1.0 if geometry.azimuth_deg > 0.0 else 1.0
        if self.frame < self._jink_until:
            # Out of plane: 55 degrees of bank off their side, nose down hard.
            return self.airframe.fly_to(telemetry, roll_deg=self._side * 55.0, pitch_deg=-12.0, throttle=0.3)
        if self.frame < self._cooldown_until:
            # Back up below them, building heading difference.
            return self.airframe.fly_to(telemetry, roll_deg=self._side * 40.0, pitch_deg=10.0, throttle=1.0)
        # Sanctuary: a turn away from their nose, not a straight line.
        away = _away(geometry.azimuth_deg)
        roll = float(np.clip(away * 0.5, -45.0, 45.0))
        return self.airframe.fly_to(telemetry, roll_deg=roll, pitch_deg=3.0, speed_kcas=self.speed_kcas)


class Reversal(_Scripted):
    """Turns hard one way and reverses when the attacker overshoots.

    AFTTP 3-3 4.3.10.4.2.2 and 4.3.10.5.7: after a merge or a close pass, a
    reversal puts a larger-radius attacker forward of the 3/9 line unless it
    cuts power; against a high line-of-sight overshoot, roll to lift-vector-on
    and the fight becomes one-circle, where the smaller radius wins. Overshoot
    is read as the attacker crossing from one side to the other at close range
    with its nose no longer on us. Otherwise it is a break turn, the hardest
    thing here to track — so the reversal is a break turn that *also* punishes
    the overshoot a tracking policy makes when it finally gets close.
    """

    name = "reversal"

    def __init__(self, *args: Any, close_m: float = 900.0, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.close_m = close_m
        self._direction = 1.0
        self._last_side = 0.0
        self._reversed_until = 0

    def reset(self) -> None:
        super().reset()
        self._direction = 1.0
        self._last_side = 0.0
        self._reversed_until = 0

    def act(self, telemetry: Telemetry) -> np.ndarray:
        geometry = self.encoder.geometry(telemetry)
        side = 1.0 if geometry.azimuth_deg > 0.0 else -1.0
        if self.frame == 1:
            # Turn away from them to begin with, like the break turn does.
            self._direction = -side
        crossed = self._last_side != 0.0 and side != self._last_side
        close = geometry.distance_m < self.close_m
        if crossed and close and self.frame >= self._reversed_until:
            # They just went past: reverse into them. Unload for a few frames
            # is implicit in the roll through wings level.
            self._direction = -self._direction
            self._reversed_until = self.frame + int(2.0 * SIM_HZ)
        self._last_side = side
        return self.airframe.fly_to(telemetry, roll_deg=self._direction * 70.0, pitch_deg=8.0, throttle=1.0)


class LeadTurn(_Scripted):
    """A merge fighter: points at the attacker, turns in before the pass.

    AFTTP 3-3 4.3.11: turning room before the 3/9 pass is only there if the
    other side gives it, and an unaware opponent gives it by flying straight
    to the merge. This one flies at the attacker (pure pursuit, the thing the
    handbook says overshoots if held) and, inside two kilometres, starts the
    turn toward the attacker's side so that it arrives at the pass with angles
    in hand, then keeps turning into it: a one-circle fight from the first
    pass. The public host starts both aircraft on opposite headings abeam
    (CONFORMANCE.md F), which is this fight.
    """

    name = "leadturn"

    def __init__(self, *args: Any, turn_in_m: float = 2000.0, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.turn_in_m = turn_in_m

    def act(self, telemetry: Telemetry) -> np.ndarray:
        geometry = self.encoder.geometry(telemetry)
        if geometry.distance_m > self.turn_in_m:
            # Pure pursuit to the merge, at speed.
            return self.airframe.fly_to(
                telemetry,
                roll_deg=float(np.clip(geometry.azimuth_deg * 1.5, -60.0, 60.0)),
                pitch_deg=float(np.clip(geometry.elevation_deg * 0.5, -10.0, 10.0)),
                throttle=1.0,
            )
        # Inside: turn hard toward them and keep the lift vector on them.
        roll = 75.0 if geometry.azimuth_deg >= 0.0 else -75.0
        return self.airframe.fly_to(telemetry, roll_deg=roll, pitch_deg=10.0, throttle=1.0)


#: Every scripted opponent, by the name a flag uses. Ordered roughly by how
#: hard they are to score against, which is the order a curriculum wants.
#: This is the scoreboard's ruler and does not change.
ADVERSARIES: dict[str, type[_Scripted]] = {
    "wanderer": Wanderer,
    "scissors": Scissors,
    "energy": EnergyFighter,
    "break": BreakTurn,
}

#: The doctrine set: reactive opponents from the handbook and APL. Pool and
#: evaluation material, not on the fixed bench.
DOCTRINE: dict[str, type[_Scripted]] = {
    "flare": Flare,
    "jinker": Jinker,
    "reversal": Reversal,
    "leadturn": LeadTurn,
}

#: Everything buildable by name.
SCRIPTED: dict[str, type[_Scripted]] = {**ADVERSARIES, **DOCTRINE}


def build(name: str, *, speed_kcas: float = 340.0, seed: int = 0) -> _Scripted:
    """One scripted opponent by name."""
    if name not in SCRIPTED:
        raise KeyError(f"unknown adversary {name!r}; have {', '.join(sorted(SCRIPTED))}")
    kind = SCRIPTED[name]
    if kind is Wanderer:
        return Wanderer(speed_kcas=speed_kcas, seed=seed)
    return kind(speed_kcas=speed_kcas)
