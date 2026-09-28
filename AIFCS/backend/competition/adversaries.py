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


#: Every scripted opponent, by the name a flag uses. Ordered roughly by how
#: hard they are to score against, which is the order a curriculum wants.
ADVERSARIES: dict[str, type[_Scripted]] = {
    "wanderer": Wanderer,
    "scissors": Scissors,
    "energy": EnergyFighter,
    "break": BreakTurn,
}


def build(name: str, *, speed_kcas: float = 340.0, seed: int = 0) -> _Scripted:
    """One scripted opponent by name."""
    if name not in ADVERSARIES:
        raise KeyError(f"unknown adversary {name!r}; have {', '.join(sorted(ADVERSARIES))}")
    kind = ADVERSARIES[name]
    if kind is Wanderer:
        return Wanderer(speed_kcas=speed_kcas, seed=seed)
    return kind(speed_kcas=speed_kcas)
