"""The 20-dimensional state the policy sees, computed once for both paths.

This reproduces the organiser's encoding exactly — same normalisations, same
rotation order, same closure-rate definition — because the reference policy and
any policy trained against the reference environment expect that layout, and
because the competition host is the one that decides what a state means.

What it does not reproduce is the loss of precision. The reference client casts
the received doubles to float32 before differencing two positions a few hundred
metres apart on a 6,378 km radius; the training environment differences JSBSim's
own float64 radians. Measured at 23 deg N, the cast quantises position to 0.212 m,
and the closure rate is a position difference multiplied by 60, so it arrives
with about +/-12.7 m/s of noise the trainer never produced. The algorithm is the
organiser's; the precision is the trainer's.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from competition.protocol import OBS_FIELDS

STATE_SIZE = 20

#: WGS-84 equatorial radius, the value the reference implementation uses.
EARTH_RADIUS_M = 6378137.0
FT_TO_M = 0.3048
#: The reference normalises speeds by a fixed 340 m/s, not by local sound speed.
REFERENCE_SPEED_MPS = 340.0
#: Host tick. Closure rate is a per-frame distance difference scaled by this.
SIM_HZ = 60.0


@dataclass(frozen=True)
class Telemetry:
    """One frame of truth, in the organiser's units.

    Built from a UDP packet in competition, and from JSBSim properties in
    training. Having both produce this means `StateEncoder` cannot tell them
    apart, which is the point: there is nothing left to drift.
    """

    own_lat_deg: float
    own_lon_deg: float
    own_alt_ft: float
    own_roll_deg: float
    own_pitch_deg: float
    own_yaw_deg: float
    own_vn_fps: float
    own_ve_fps: float
    own_vd_fps: float
    own_p_radps: float
    own_q_radps: float
    own_r_radps: float
    own_vc_fps: float
    own_vt_fps: float
    own_g_acc: float
    own_u_fps: float
    own_v_fps: float
    own_w_fps: float
    own_alpha_deg: float
    own_beta_deg: float
    enemy_lat_deg: float
    enemy_lon_deg: float
    enemy_alt_ft: float
    enemy_vn_fps: float
    enemy_ve_fps: float
    enemy_vd_fps: float

    @classmethod
    def from_observation(cls, obs: np.ndarray) -> Telemetry:
        """From the 26 values of an OBS packet, in ICD order."""
        if obs.shape != (len(OBS_FIELDS),):
            raise ValueError(f"expected {len(OBS_FIELDS)} values, got {obs.shape}")
        return cls(**{name: float(obs[i]) for i, name in enumerate(OBS_FIELDS)})

    def as_observation(self) -> np.ndarray:
        """Back to ICD order. Lets the training side speak the wire format."""
        return np.array([getattr(self, name) for name in OBS_FIELDS], dtype=np.float64)

    @property
    def reference_mach(self) -> float:
        """True airspeed over a fixed 340 m/s.

        Not Mach: it ignores altitude, where the real speed of sound falls by
        about 15% between sea level and 30,000 ft. It is used anyway, because
        the OBS packet carries no Mach and no temperature, so this is the only
        speed measure available on the day — and a limit that bites at one speed
        in training and another in competition is worse than an imprecise one.
        """
        return self.own_vt_fps * FT_TO_M / REFERENCE_SPEED_MPS


@dataclass
class StateEncoder:
    """Turns telemetry into the policy's input, carrying what needs carrying.

    Closure rate is the only term with memory, and memory is what the reference
    client gets wrong: its `prev_distance` is a module global that survives the
    end of a round, so the first frame of round two is computed against round
    one's final geometry. Here it is instance state, and `reset()` is a method
    someone can be made to call.
    """

    prev_distance_m: float | None = None

    def reset(self) -> None:
        """Forget the previous frame. Call this at the start of every round."""
        self.prev_distance_m = None

    def encode(self, telemetry: Telemetry) -> np.ndarray:
        own_alt_m = telemetry.own_alt_ft * FT_TO_M
        enemy_alt_m = telemetry.enemy_alt_ft * FT_TO_M

        lat1 = math.radians(telemetry.own_lat_deg)
        lon1 = math.radians(telemetry.own_lon_deg)
        lat2 = math.radians(telemetry.enemy_lat_deg)
        lon2 = math.radians(telemetry.enemy_lon_deg)

        # A local tangent plane. Good for the few kilometres a round covers.
        north = (lat2 - lat1) * EARTH_RADIUS_M
        east = (lon2 - lon1) * EARTH_RADIUS_M * math.cos(lat1)
        down = -(enemy_alt_m - own_alt_m)
        relative_ned = np.array([north, east, down], dtype=np.float64)
        distance_m = float(np.linalg.norm(relative_ned))

        # First frame of a round has no previous distance, so closure is zero
        # rather than a number invented from the round before.
        if self.prev_distance_m is None:
            self.prev_distance_m = distance_m
        closure_mps = (self.prev_distance_m - distance_m) * SIM_HZ
        self.prev_distance_m = distance_m

        enemy_velocity_ned = (
            np.array(
                [telemetry.enemy_vn_fps, telemetry.enemy_ve_fps, telemetry.enemy_vd_fps],
                dtype=np.float64,
            )
            * FT_TO_M
        )
        enemy_aspect_deg = _angle_off_tail(relative_ned, enemy_velocity_ned)

        phi = math.radians(telemetry.own_roll_deg)
        theta = math.radians(telemetry.own_pitch_deg)
        psi = math.radians(telemetry.own_yaw_deg)
        body = _ned_to_body(phi, theta, psi) @ relative_ned
        xb, yb, zb = body

        azimuth_deg = math.degrees(math.atan2(yb, xb))
        elevation_deg = math.degrees(math.atan2(-zb, math.hypot(xb, yb)))

        state = np.zeros(STATE_SIZE, dtype=np.float32)
        state[0] = distance_m / 5000.0
        state[1] = (enemy_alt_m - own_alt_m) / 5000.0
        state[2] = elevation_deg / 90.0
        state[3] = azimuth_deg / 180.0
        state[4] = enemy_aspect_deg / 180.0
        state[5] = math.sin(phi)
        state[6] = math.cos(phi)
        state[7] = math.sin(theta)
        state[8] = math.cos(theta)
        state[9] = telemetry.own_alpha_deg / 30.0
        state[10] = telemetry.own_beta_deg / 30.0
        state[11] = own_alt_m / 5000.0
        state[12] = telemetry.own_vt_fps * FT_TO_M / REFERENCE_SPEED_MPS
        state[13] = closure_mps / REFERENCE_SPEED_MPS
        state[14] = telemetry.own_u_fps * FT_TO_M / REFERENCE_SPEED_MPS
        state[15] = telemetry.own_v_fps * FT_TO_M / REFERENCE_SPEED_MPS
        state[16] = telemetry.own_w_fps * FT_TO_M / REFERENCE_SPEED_MPS
        state[17] = telemetry.own_p_radps
        state[18] = telemetry.own_q_radps
        state[19] = telemetry.own_r_radps
        return state

    def geometry(self, telemetry: Telemetry) -> Geometry:
        """The angles and range in degrees and metres, for scoring and rewards.

        Separate from `encode` because a reward computed from the normalised
        state has to undo the normalisation first, and a term that multiplies
        by 5000 to get back what was divided by 5000 is a place for a mistake.
        """
        own_alt_m = telemetry.own_alt_ft * FT_TO_M
        enemy_alt_m = telemetry.enemy_alt_ft * FT_TO_M
        lat1 = math.radians(telemetry.own_lat_deg)
        relative_ned = np.array(
            [
                (math.radians(telemetry.enemy_lat_deg) - lat1) * EARTH_RADIUS_M,
                (math.radians(telemetry.enemy_lon_deg) - math.radians(telemetry.own_lon_deg))
                * EARTH_RADIUS_M
                * math.cos(lat1),
                -(enemy_alt_m - own_alt_m),
            ],
            dtype=np.float64,
        )
        distance_m = float(np.linalg.norm(relative_ned))
        body = (
            _ned_to_body(
                math.radians(telemetry.own_roll_deg),
                math.radians(telemetry.own_pitch_deg),
                math.radians(telemetry.own_yaw_deg),
            )
            @ relative_ned
        )
        xb, yb, zb = body
        enemy_velocity_ned = (
            np.array(
                [telemetry.enemy_vn_fps, telemetry.enemy_ve_fps, telemetry.enemy_vd_fps],
                dtype=np.float64,
            )
            * FT_TO_M
        )
        return Geometry(
            distance_m=distance_m,
            track_angle_deg=math.degrees(math.acos(float(np.clip(xb / (distance_m + 1e-5), -1.0, 1.0)))),
            azimuth_deg=math.degrees(math.atan2(yb, xb)),
            elevation_deg=math.degrees(math.atan2(-zb, math.hypot(xb, yb))),
            aspect_angle_deg=_angle_off_tail(relative_ned, enemy_velocity_ned),
            own_alt_m=own_alt_m,
            enemy_alt_m=enemy_alt_m,
        )


@dataclass(frozen=True)
class Geometry:
    """Engagement geometry in the units the rules are written in."""

    distance_m: float
    #: Track angle: between our nose and the line of sight to the target.
    track_angle_deg: float
    azimuth_deg: float
    elevation_deg: float
    #: Aspect angle: between the target's tail and the line of sight to us.
    aspect_angle_deg: float
    own_alt_m: float
    enemy_alt_m: float

    @property
    def distance_ft(self) -> float:
        return self.distance_m / FT_TO_M


def _ned_to_body(phi: float, theta: float, psi: float) -> np.ndarray:
    """Rx(roll) @ Ry(pitch) @ Rz(yaw), the reference implementation's order."""
    rx = np.array(
        [[1.0, 0.0, 0.0], [0.0, math.cos(phi), math.sin(phi)], [0.0, -math.sin(phi), math.cos(phi)]]
    )
    ry = np.array(
        [
            [math.cos(theta), 0.0, -math.sin(theta)],
            [0.0, 1.0, 0.0],
            [math.sin(theta), 0.0, math.cos(theta)],
        ]
    )
    rz = np.array(
        [[math.cos(psi), math.sin(psi), 0.0], [-math.sin(psi), math.cos(psi), 0.0], [0.0, 0.0, 1.0]]
    )
    return rx @ ry @ rz


def _angle_off_tail(relative_ned: np.ndarray, enemy_velocity_ned: np.ndarray) -> float:
    """Angle between where the target is going and where we are, in degrees.

    180 when the target is pointing straight away from us, 0 when straight at
    us. A target with no measurable velocity is treated as pointing away, which
    is the reference behaviour and the harmless direction to be wrong in.
    """
    line_of_sight = -relative_ned  # from the target, towards us
    speed = float(np.linalg.norm(enemy_velocity_ned))
    range_m = float(np.linalg.norm(line_of_sight))
    if speed < 1e-5 or range_m < 1e-5:
        return 180.0
    cosine = float(np.clip(np.dot(enemy_velocity_ned, line_of_sight) / (speed * range_m), -1.0, 1.0))
    return math.degrees(math.acos(cosine))
