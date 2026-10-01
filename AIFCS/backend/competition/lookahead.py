"""`lookahead`: the extended observation plus where the geometry is *going*.

Three places say the same thing. Heron, who won AlphaDogfight at 10 Hz, said
the agent "had to know its trajectory for the next 3 seconds to keep the
opponent within the 1-degree cone" (SRC-020). The USAF handbook's offensive
chapter says, against a jinking target, do not chase the pipper: "predict
where the defender will come out of the jink and have the gun cross in lead"
(AFTTP 3-3 Vol 5, 4.3.9.7.2.2.2, SRC-022). The Korean winner fed its network
aim and range *margins*, not just the current aim (SRC-012). All of them are
lead computing: the gun solution is a prediction, not a measurement.

A policy can learn to predict from rates, and `extended` already carries the
rates. This encoder does the first step of that prediction for it, the way a
lead-computing gunsight does for a pilot: dead-reckon both aircraft along
their current velocity vectors for one and three seconds and report the
geometry that results, in the body frame the policy already reads. Constant
velocity is wrong whenever anyone turns, which is exactly when it matters;
that is on purpose. The honest prediction is cheap and the correction to it
is what there is to learn. Nothing here reads anything the OBS packet does not
carry, and nothing is sent to the host (rule R1: compute it yourself).
"""

from __future__ import annotations

import math

import numpy as np

from competition.features import EXTENDED_STATE_SIZE, ExtendedEncoder
from competition.state import FT_TO_M, REFERENCE_SPEED_MPS, Telemetry, _ned_to_body

EARTH_RADIUS_M = 6_371_000.0

#: Seconds ahead. One second is the next decision at 10 Hz plus the stick's
#: slew; three is Heron's number and about how long a snap takes to set up.
HORIZONS_S: tuple[float, ...] = (1.0, 3.0)

#: Per horizon, in order. Azimuth and elevation of where they will be, in our
#: current body frame; the range then; the track angle then; and the closure
#: rate the two imply.
LOOKAHEAD_FIELDS: tuple[str, ...] = (
    "predicted_azimuth",
    "predicted_elevation",
    "predicted_range",
    "predicted_track",
    "predicted_closure",
)
PER_HORIZON = len(LOOKAHEAD_FIELDS)
LOOKAHEAD_SIZE = PER_HORIZON * len(HORIZONS_S)
LOOKAHEAD_STATE_SIZE = EXTENDED_STATE_SIZE + LOOKAHEAD_SIZE

#: Ranges normalised by the far end of what the rules call reconnaissance.
RANGE_NORMALISER_M = 10_000.0


def relative_ned_m(telemetry: Telemetry) -> np.ndarray:
    """Them minus us, in metres, north-east-down."""
    lat1 = math.radians(telemetry.own_lat_deg)
    north = math.radians(telemetry.enemy_lat_deg - telemetry.own_lat_deg) * EARTH_RADIUS_M
    east = math.radians(telemetry.enemy_lon_deg - telemetry.own_lon_deg) * EARTH_RADIUS_M * math.cos(lat1)
    down = -(telemetry.enemy_alt_ft - telemetry.own_alt_ft) * FT_TO_M
    return np.array([north, east, down], dtype=np.float64)


def predicted_geometry(telemetry: Telemetry, seconds: float) -> tuple[float, float, float, float, float]:
    """(azimuth_deg, elevation_deg, range_m, track_deg, closure_mps) `seconds` ahead.

    Both aircraft are moved along their current velocity vectors; the result
    is expressed in *today's* body frame, so "predicted azimuth" answers the
    question the stick asks: how far do I have to turn to be pointing at
    where they will be.
    """
    relative = relative_ned_m(telemetry)
    own_vel = np.array([telemetry.own_vn_fps, telemetry.own_ve_fps, telemetry.own_vd_fps]) * FT_TO_M
    tgt_vel = np.array([telemetry.enemy_vn_fps, telemetry.enemy_ve_fps, telemetry.enemy_vd_fps]) * FT_TO_M
    future = relative + (tgt_vel - own_vel) * seconds
    now_m = float(np.linalg.norm(relative))
    then_m = float(np.linalg.norm(future))
    to_body = _ned_to_body(
        math.radians(telemetry.own_roll_deg),
        math.radians(telemetry.own_pitch_deg),
        math.radians(telemetry.own_yaw_deg),
    )
    xb, yb, zb = to_body @ future
    azimuth = math.degrees(math.atan2(yb, xb))
    elevation = math.degrees(math.atan2(-zb, math.hypot(xb, yb)))
    track = math.degrees(math.acos(max(-1.0, min(1.0, xb / (then_m + 1e-6)))))
    closure = (now_m - then_m) / seconds
    return azimuth, elevation, then_m, track, closure


class LookaheadEncoder(ExtendedEncoder):
    """`extended`, then the predicted geometry at each horizon."""

    def encode(self, telemetry: Telemetry) -> np.ndarray:
        base = super().encode(telemetry)
        extra: list[float] = []
        for seconds in HORIZONS_S:
            azimuth, elevation, range_m, track, closure = predicted_geometry(telemetry, seconds)
            extra.extend(
                [
                    azimuth / 180.0,
                    elevation / 90.0,
                    min(range_m / RANGE_NORMALISER_M, 2.0),
                    track / 180.0,
                    closure / REFERENCE_SPEED_MPS,
                ]
            )
        return np.concatenate([base, np.array(extra, dtype=np.float32)])
