"""Left-right mirroring of transitions, for a replay buffer that learns both hands.

A dogfight has no preferred side: a round seen in a mirror is a round. A
transition (s, a, r, s') therefore has a twin (Ms, Aa, r, Ms') where M flips
every lateral quantity in the observation and A flips aileron and rudder, and
the twin is free. The winners of the same problem in Korea stored both by
default (research/sources.yaml SRC-012, "mirror augmentation"); this is that,
for SAC's replay buffer.

**Measured, not assumed.** The JSBSim F-16 is not exactly symmetric: two
aircraft flown with mirrored random full-authority stick diverge to 12 degrees
of bank after ten seconds, and the divergence is the same from any heading, so
it is the model rather than the rotating Earth. With the lateral stick centred
the two stay within 0.001 degrees a second, so there is no constant lateral
bias — the difference is a tiny asymmetry amplified by ten seconds of violent
manoeuvre. Per frame, which is what a stored transition spans, the roll
asymmetry grew by at most 0.005 degrees in the first second. A mirrored
transition is wrong by that much and no more, and `tests/test_competition_mirror.py`
holds the bound.

Off by default. `train.py --mirror` turns it on for SAC; it is a research-tier
change to how experience is stored and has no meaning for PPO, which learns
from the trajectory it just ran.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from competition.features import EXTENDED_STATE_SIZE, EXTRA_FIELDS
from competition.state import STATE_SIZE, Telemetry

#: The reference twenty (state.py, in order). -1 where a quantity changes sign
#: in a mirror: azimuth, sin(roll), sideslip, lateral body velocity, roll rate
#: and yaw rate. Aspect angle is an unsigned angle off the tail, so it holds.
REFERENCE_SIGNS = np.array(
    [
        +1,  # 0  distance
        +1,  # 1  altitude difference
        +1,  # 2  elevation
        -1,  # 3  azimuth
        +1,  # 4  aspect (angle off tail, 0..180)
        -1,  # 5  sin(roll)
        +1,  # 6  cos(roll)
        +1,  # 7  sin(pitch)
        +1,  # 8  cos(pitch)
        +1,  # 9  alpha
        -1,  # 10 beta
        +1,  # 11 altitude
        +1,  # 12 true airspeed
        +1,  # 13 closure
        +1,  # 14 u
        -1,  # 15 v
        +1,  # 16 w
        -1,  # 17 p
        +1,  # 18 q
        -1,  # 19 r
    ],
    dtype=np.float32,
)
assert REFERENCE_SIGNS.shape == (STATE_SIZE,)

#: The ten extras (features.py, in order). Only the azimuth rate is lateral;
#: the enemy's turn rate is an unsigned swing of its velocity vector.
_EXTRA_SIGNS = {
    "own_g_load": +1,
    "own_calibrated_speed": +1,
    "enemy_speed": +1,
    "own_specific_energy": +1,
    "energy_advantage": +1,
    "azimuth_rate": -1,
    "elevation_rate": +1,
    "aspect_rate": +1,
    "enemy_turn_rate": +1,
    "round_elapsed": +1,
}
EXTENDED_SIGNS = np.concatenate(
    [REFERENCE_SIGNS, np.array([_EXTRA_SIGNS[name] for name in EXTRA_FIELDS], dtype=np.float32)]
)
assert EXTENDED_SIGNS.shape == (EXTENDED_STATE_SIZE,)

#: Every frame here has x forward, y to the right and z down (world: north,
#: east, down), so a mirror negates the y component of every ordinary vector
#: in every frame. Angular rate is an axial vector and goes the other way: x
#: and z negate, y holds — the same rule that makes p and r flip and q stay.
_ORDINARY = np.array([+1, -1, +1], dtype=np.float32)
_AXIAL = np.array([-1, +1, -1], dtype=np.float32)


def _frames_signs() -> np.ndarray:
    from competition.frames import PAIRS

    return np.concatenate(
        [EXTENDED_SIGNS] + [_AXIAL if vector == "own_omega" else _ORDINARY for vector, _ in PAIRS]
    )


#: The four channels: aileron, elevator, rudder, throttle.
ACTION_SIGNS = np.array([-1.0, 1.0, -1.0, 1.0], dtype=np.float64)


def observation_signs(width: int) -> np.ndarray:
    """The sign vector for an observation of this width, or a refusal.

    By width rather than by name because the replay buffer only ever sees the
    observation space, and there are exactly two encodings.
    """
    if width == STATE_SIZE:
        return REFERENCE_SIGNS
    if width == EXTENDED_STATE_SIZE:
        return EXTENDED_SIGNS
    from competition.frames import FRAMES_STATE_SIZE

    if width == FRAMES_STATE_SIZE:
        return _frames_signs()
    from competition.lookahead import LOOKAHEAD_STATE_SIZE

    if width == LOOKAHEAD_STATE_SIZE:
        return _lookahead_signs()
    raise ValueError(
        f"no mirror map for a {width}-wide observation; only the reference "
        f"({STATE_SIZE}), extended ({EXTENDED_STATE_SIZE}), frames "
        f"({FRAMES_STATE_SIZE}) and lookahead ({LOOKAHEAD_STATE_SIZE}) encodings have one"
    )


def _lookahead_signs() -> np.ndarray:
    """Extended, then per horizon: azimuth flips, elevation, range, track and
    closure do not. A mirrored world puts the predicted target on the other
    side and leaves how far and how soon alone."""
    from competition.lookahead import HORIZONS_S, LOOKAHEAD_FIELDS

    per_horizon = [-1.0 if field == "predicted_azimuth" else 1.0 for field in LOOKAHEAD_FIELDS]
    return np.concatenate([EXTENDED_SIGNS, np.array(per_horizon * len(HORIZONS_S), dtype=np.float64)])


def mirror_observation(observation: np.ndarray) -> np.ndarray:
    """The same frame seen from the other hand. Works on a batch too."""
    signs = observation_signs(int(np.shape(observation)[-1]))
    return np.asarray(observation) * signs


def mirror_action_scaled(scaled: np.ndarray, action_space: Any) -> np.ndarray:
    """Mirror an action as the buffer stores it: scaled to [-1, 1] per channel.

    The scaling is affine on each channel's bounds, so a mirror in physical
    units is not a sign flip in scaled units unless the bounds are symmetric.
    They are not for the pinned rudder, whose box is [0, 1e-17]: a rudder of
    0 is stored as -1, and negating that would be full right rudder. So:
    unscale, flip the two lateral channels, clip to the box, rescale.
    """
    low = np.asarray(action_space.low, dtype=np.float64)
    high = np.asarray(action_space.high, dtype=np.float64)
    scaled = np.asarray(scaled, dtype=np.float64)
    physical = low + 0.5 * (scaled + 1.0) * (high - low)
    physical = np.clip(physical * ACTION_SIGNS, low, high)
    span = np.where(high - low > 0, high - low, 1.0)
    return 2.0 * (physical - low) / span - 1.0


def mirror_telemetry(telemetry: Telemetry, about_lon_deg: float) -> Telemetry:
    """The same instant reflected in a north-south vertical plane.

    Used by the tests to check that mirroring the *input* of an encoder gives
    the sign-flipped *output*; training never builds one of these, it flips
    encoded observations directly.
    """
    return Telemetry(
        own_lat_deg=telemetry.own_lat_deg,
        own_lon_deg=2.0 * about_lon_deg - telemetry.own_lon_deg,
        own_alt_ft=telemetry.own_alt_ft,
        own_roll_deg=-telemetry.own_roll_deg,
        own_pitch_deg=telemetry.own_pitch_deg,
        own_yaw_deg=(-telemetry.own_yaw_deg) % 360.0,
        own_vn_fps=telemetry.own_vn_fps,
        own_ve_fps=-telemetry.own_ve_fps,
        own_vd_fps=telemetry.own_vd_fps,
        own_p_radps=-telemetry.own_p_radps,
        own_q_radps=telemetry.own_q_radps,
        own_r_radps=-telemetry.own_r_radps,
        own_vc_fps=telemetry.own_vc_fps,
        own_vt_fps=telemetry.own_vt_fps,
        own_g_acc=telemetry.own_g_acc,
        own_u_fps=telemetry.own_u_fps,
        own_v_fps=-telemetry.own_v_fps,
        own_w_fps=telemetry.own_w_fps,
        own_alpha_deg=telemetry.own_alpha_deg,
        own_beta_deg=-telemetry.own_beta_deg,
        enemy_lat_deg=telemetry.enemy_lat_deg,
        enemy_lon_deg=2.0 * about_lon_deg - telemetry.enemy_lon_deg,
        enemy_alt_ft=telemetry.enemy_alt_ft,
        enemy_vn_fps=telemetry.enemy_vn_fps,
        enemy_ve_fps=-telemetry.enemy_ve_fps,
        enemy_vd_fps=telemetry.enemy_vd_fps,
    )


def _replay_buffer_base() -> Any:
    from stable_baselines3.common.buffers import ReplayBuffer

    return ReplayBuffer


class MirroredReplayBuffer(_replay_buffer_base()):  # type: ignore[misc]
    """SAC's replay buffer, storing every transition and its mirror image.

    Each `add` becomes two, so `buffer_size` holds half as many real frames.
    The reward is shared: everything the rewards and the score look at —
    range, track angle, aspect, altitude, G — is unchanged by a mirror.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.signs = observation_signs(int(np.prod(self.obs_shape)))

    def add(
        self,
        obs: np.ndarray,
        next_obs: np.ndarray,
        action: np.ndarray,
        reward: np.ndarray,
        done: np.ndarray,
        infos: list[dict[str, Any]],
    ) -> None:
        super().add(obs, next_obs, action, reward, done, infos)
        super().add(
            np.asarray(obs) * self.signs,
            np.asarray(next_obs) * self.signs,
            mirror_action_scaled(action, self.action_space),
            reward,
            done,
            infos,
        )


__all__ = [
    "ACTION_SIGNS",
    "EXTENDED_SIGNS",
    "REFERENCE_SIGNS",
    "MirroredReplayBuffer",
    "mirror_action_scaled",
    "mirror_observation",
    "mirror_telemetry",
    "observation_signs",
]
