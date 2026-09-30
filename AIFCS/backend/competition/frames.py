"""The extended observation, plus the same few vectors seen from several frames.

The winners of the same problem in Korea (research/sources.yaml SRC-012) fed
their policy seven direction vectors expressed in six coordinate frames —
world, own body, target body, own velocity, target velocity, line of sight —
and argued that a network given the geometry already rotated learns faster
than one that has to learn the rotations from Euler angles. This is that
idea on the 26 numbers 表 1 actually sends.

What the packet allows. We have our own attitude and body rates, both
positions, and both NED velocities. We do not have the target's attitude or
its rates, so there is no target body frame and no target angular rate;
those are left out rather than invented. What remains:

    vectors  gravity, line of sight, own velocity, target velocity,
             relative velocity, own angular rate
    frames   world (NED), own body, own velocity, target velocity,
             line of sight

with the constant or already-present combinations dropped: gravity in the
world frame, the line of sight in its own frame, a velocity in its own
frame, and our angular rate in our body frame (that is p, q, r, already in
the reference twenty). 75 numbers on top of the extended thirty.

A velocity or line-of-sight frame has its x axis along the vector and its
roll fixed by gravity: y is horizontal, z has a downward component. Straight
up or down leaves that undefined, and north is used for y then; a policy
will meet it for a frame or two in a vertical manoeuvre.

Everything here is derived from the same packet, so it is inside the rules
the way `extended` is (公告說明 一.1.(2)); and like `extended` it ends
compatibility with policies trained on the other encodings.
"""

from __future__ import annotations

import math

import numpy as np

from competition.features import EXTENDED_STATE_SIZE, ExtendedEncoder
from competition.state import EARTH_RADIUS_M, FT_TO_M, REFERENCE_SPEED_MPS, Telemetry, _ned_to_body

#: Angular rates through tanh(omega / RATE_SCALE), as SRC-012 does, so a
#: violent rate saturates rather than dominating.
RATE_SCALE_RADPS = 4.0

VECTORS: tuple[str, ...] = ("gravity", "los", "own_vel", "tgt_vel", "rel_vel", "own_omega")
FRAMES: tuple[str, ...] = ("world", "mybody", "myvel", "tgtvel", "los")

#: (vector, frame) pairs that are constant, or already in the reference twenty.
DEGENERATE: frozenset[tuple[str, str]] = frozenset(
    {
        ("gravity", "world"),
        ("los", "los"),
        ("own_vel", "myvel"),
        ("tgt_vel", "tgtvel"),
        ("own_omega", "mybody"),
    }
)

PAIRS: tuple[tuple[str, str], ...] = tuple(
    (vector, frame) for vector in VECTORS for frame in FRAMES if (vector, frame) not in DEGENERATE
)
FRAME_FIELDS: tuple[str, ...] = tuple(f"{vector}@{frame}.{axis}" for vector, frame in PAIRS for axis in "xyz")
FRAMES_EXTRA_SIZE = len(FRAME_FIELDS)
FRAMES_STATE_SIZE = EXTENDED_STATE_SIZE + FRAMES_EXTRA_SIZE

DOWN = np.array([0.0, 0.0, 1.0])
NORTH = np.array([1.0, 0.0, 0.0])


def _unit(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 1e-9 else np.zeros(3)


def direction_frame(direction_ned: np.ndarray) -> np.ndarray:
    """Rows are the frame's axes in NED: x along `direction`, roll fixed by gravity.

    Returns the 3x3 matrix that takes an NED vector into the frame. A zero or
    vertical direction has no horizontal to fix the roll with, and uses north.
    """
    x = _unit(direction_ned)
    if not np.any(x):
        x = NORTH.copy()
    y = np.cross(DOWN, x)
    if float(np.linalg.norm(y)) < 1e-6:
        y = np.cross(x, NORTH)
        if float(np.linalg.norm(y)) < 1e-6:
            y = np.array([0.0, 1.0, 0.0])
    y = _unit(y)
    z = np.cross(x, y)
    return np.vstack([x, y, z])


class FramesEncoder(ExtendedEncoder):
    """`extended`, then the vectors above in the frames above."""

    def encode(self, telemetry: Telemetry) -> np.ndarray:
        base = super().encode(telemetry)

        # --- the vectors, in NED
        lat1 = math.radians(telemetry.own_lat_deg)
        north = math.radians(telemetry.enemy_lat_deg - telemetry.own_lat_deg) * EARTH_RADIUS_M
        east = math.radians(telemetry.enemy_lon_deg - telemetry.own_lon_deg) * EARTH_RADIUS_M * math.cos(lat1)
        down = -(telemetry.enemy_alt_ft - telemetry.own_alt_ft) * FT_TO_M
        los = _unit(np.array([north, east, down]))
        own_vel = np.array([telemetry.own_vn_fps, telemetry.own_ve_fps, telemetry.own_vd_fps]) * FT_TO_M
        tgt_vel = np.array([telemetry.enemy_vn_fps, telemetry.enemy_ve_fps, telemetry.enemy_vd_fps]) * FT_TO_M
        rel_vel = tgt_vel - own_vel
        # Angular rate is given in the body frame; take it to NED so it can be
        # expressed like the others.
        to_body = _ned_to_body(
            math.radians(telemetry.own_roll_deg),
            math.radians(telemetry.own_pitch_deg),
            math.radians(telemetry.own_yaw_deg),
        )
        omega_body = np.array([telemetry.own_p_radps, telemetry.own_q_radps, telemetry.own_r_radps])
        omega_ned = to_body.T @ omega_body

        vectors = {
            "gravity": DOWN,
            "los": los,
            "own_vel": own_vel / REFERENCE_SPEED_MPS,
            "tgt_vel": tgt_vel / REFERENCE_SPEED_MPS,
            "rel_vel": rel_vel / REFERENCE_SPEED_MPS,
            "own_omega": omega_ned,
        }
        frames = {
            "world": np.eye(3),
            "mybody": to_body,
            "myvel": direction_frame(own_vel),
            "tgtvel": direction_frame(tgt_vel),
            "los": direction_frame(los),
        }

        extra = np.empty(FRAMES_EXTRA_SIZE, dtype=np.float32)
        index = 0
        for vector, frame in PAIRS:
            expressed = frames[frame] @ vectors[vector]
            if vector == "own_omega":
                expressed = np.tanh(expressed / RATE_SCALE_RADPS)
            extra[index : index + 3] = expressed
            index += 3
        return np.concatenate([base, extra])


def frames_observation_space(bound: float = 100.0):
    from gymnasium.spaces import Box

    return Box(low=-bound, high=bound, shape=(FRAMES_STATE_SIZE,), dtype=np.float64)


__all__ = [
    "DEGENERATE",
    "FRAMES",
    "FRAMES_EXTRA_SIZE",
    "FRAMES_STATE_SIZE",
    "FRAME_FIELDS",
    "PAIRS",
    "RATE_SCALE_RADPS",
    "VECTORS",
    "FramesEncoder",
    "direction_frame",
    "frames_observation_space",
]
