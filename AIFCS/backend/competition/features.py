"""An extended observation, built from the same 26 doubles the host sends.

The 20-dimensional state in `state.py` is the reference package's encoding. It
is reproduced exactly there and is not touched here — a reference policy has to
keep loading, and the differential test against the organiser's own function
has to keep passing. This is an addition alongside it, chosen per session.

What the reference encoding leaves out, all of it derivable from 表 1 with no
extra information from anywhere:

* **Our own G.** 表 1 field 15 is `accelerations/n-pilot-z-norm` and the
  scoring subtracts 1000 a second above 9G. The reference state does not
  include it, so a policy is punished for a quantity it cannot observe. That
  is not a hard problem to learn around; it is an impossible one.

  The sign is worth stating because it is the opposite of the intuition:
  measured here, level flight reads -0.40, a hard pull -4.24 and a pushover
  +3.75. Body Z points down, so a positive-G pull is negative in this field.
  Kept as published rather than flipped, and the scoring's `abs()` is correct
  for it — that catches a 9G pull and a -9G pushover alike.
* **How fast the geometry is changing.** Azimuth, elevation and aspect are all
  in the state as positions. None of their rates is, so a policy cannot tell
  a closing turn from an opening one without remembering, and it has no memory.
* **The opponent's speed.** Their velocity vector arrives in fields 24-26 and
  is used only to work out the aspect angle. The magnitude is thrown away.
* **Energy.** Specific energy — height plus speed squared over 2g — is the
  quantity a turning fight is actually about, and neither side's is present.
* **Time.** A kill at ten seconds scores 290 more than one at three hundred,
  and a round that reaches time is decided on the margin. The policy has no
  idea how long is left.

Adding these ends compatibility with the organiser's own policies, which
expect twenty inputs. That is the trade, and it is why this is a choice rather
than a change.
"""

from __future__ import annotations

import math

import numpy as np

from competition.state import (
    FT_TO_M,
    REFERENCE_SPEED_MPS,
    SIM_HZ,
    STATE_SIZE,
    Geometry,
    StateEncoder,
    Telemetry,
)

#: Gravity, for specific energy.
G_MPS2 = 9.80665
#: The scoring's G threshold, used as the normaliser so that 1.0 means "at the
#: limit" rather than an arbitrary fraction.
G_NORMALISER = 9.0
#: Specific energy runs to a few thousand metres; this keeps it near 1.
ENERGY_NORMALISER = 10_000.0
#: Angular rates in degrees per second, normalised by a brisk rate.
RATE_NORMALISER = 90.0

EXTRA_SIZE = 10
EXTENDED_STATE_SIZE = STATE_SIZE + EXTRA_SIZE

#: What each added dimension is, in order. Kept as data because a list of
#: names next to the code that fills them is the only thing that stops the two
#: drifting, and because a model card should be able to say what it was fed.
EXTRA_FIELDS: tuple[str, ...] = (
    "own_g_load",
    "own_calibrated_speed",
    "enemy_speed",
    "own_specific_energy",
    "energy_advantage",
    "azimuth_rate",
    "elevation_rate",
    "aspect_rate",
    "enemy_turn_rate",
    "round_elapsed",
)


def specific_energy_m(altitude_m: float, speed_mps: float) -> float:
    """Height plus the height that speed could buy, which is what a turn spends."""
    return altitude_m + speed_mps * speed_mps / (2.0 * G_MPS2)


class ExtendedEncoder:
    """The reference twenty, then ten more.

    Holds the previous frame's geometry so it can report rates. Reset between
    rounds for exactly the reason the reference encoder is: a rate computed
    across a round boundary is a number about nothing.
    """

    def __init__(self, round_seconds: float = 300.0) -> None:
        self.base = StateEncoder()
        self.round_seconds = round_seconds
        self._previous: Geometry | None = None
        self._previous_enemy_velocity: np.ndarray | None = None
        self._frame = 0

    # The round and the client both call these on the base encoder, so the
    # extended one has to answer to the same names.
    def reset(self) -> None:
        self.base.reset()
        self._previous = None
        self._previous_enemy_velocity = None
        self._frame = 0

    def geometry(self, telemetry: Telemetry) -> Geometry:
        return self.base.geometry(telemetry)

    def encode(self, telemetry: Telemetry) -> np.ndarray:
        # The reference twenty, unchanged and computed by the reference code.
        base = self.base.encode(telemetry)
        geometry = self.base.geometry(telemetry)

        own_speed = telemetry.own_vt_fps * FT_TO_M
        enemy_velocity = (
            np.array(
                [telemetry.enemy_vn_fps, telemetry.enemy_ve_fps, telemetry.enemy_vd_fps],
                dtype=np.float64,
            )
            * FT_TO_M
        )
        enemy_speed = float(np.linalg.norm(enemy_velocity))

        own_energy = specific_energy_m(geometry.own_alt_m, own_speed)
        enemy_energy = specific_energy_m(geometry.enemy_alt_m, enemy_speed)

        # Rates are zero on the first frame of a round rather than a difference
        # against whatever the last round ended on.
        if self._previous is None:
            azimuth_rate = elevation_rate = aspect_rate = 0.0
        else:
            azimuth_rate = _wrapped(geometry.azimuth_deg - self._previous.azimuth_deg) * SIM_HZ
            elevation_rate = (geometry.elevation_deg - self._previous.elevation_deg) * SIM_HZ
            aspect_rate = _wrapped(geometry.aspect_angle_deg - self._previous.aspect_angle_deg) * SIM_HZ
        self._previous = geometry

        if self._previous_enemy_velocity is None or enemy_speed < 1e-6:
            enemy_turn_rate = 0.0
        else:
            # How far their velocity vector swung, in degrees per second. Their
            # angular rates are not in the packet; this is what can be had.
            previous = self._previous_enemy_velocity
            cosine = float(np.dot(previous, enemy_velocity) / (np.linalg.norm(previous) * enemy_speed + 1e-9))
            enemy_turn_rate = math.degrees(math.acos(max(-1.0, min(1.0, cosine)))) * SIM_HZ
        self._previous_enemy_velocity = enemy_velocity

        self._frame += 1
        elapsed = self._frame / SIM_HZ

        extra = np.array(
            [
                telemetry.own_g_acc / G_NORMALISER,
                telemetry.own_vc_fps * FT_TO_M / REFERENCE_SPEED_MPS,
                enemy_speed / REFERENCE_SPEED_MPS,
                own_energy / ENERGY_NORMALISER,
                (own_energy - enemy_energy) / ENERGY_NORMALISER,
                azimuth_rate / RATE_NORMALISER,
                elevation_rate / RATE_NORMALISER,
                aspect_rate / RATE_NORMALISER,
                enemy_turn_rate / RATE_NORMALISER,
                min(elapsed / self.round_seconds, 1.0),
            ],
            dtype=np.float32,
        )
        return np.concatenate([base, extra])


def _wrapped(difference_deg: float) -> float:
    """A bearing difference, taken the short way round.

    Azimuth runs -180 to 180, so a nose crossing the tail steps 360 degrees in
    one frame and would otherwise be reported as a 21,600 degree-per-second
    turn.
    """
    return (difference_deg + 180.0) % 360.0 - 180.0


def extended_observation_space(bound: float = 100.0):
    """Bounds for the longer state, in the shape the reference uses."""
    from gymnasium.spaces import Box

    return Box(low=-bound, high=bound, shape=(EXTENDED_STATE_SIZE,), dtype=np.float64)
