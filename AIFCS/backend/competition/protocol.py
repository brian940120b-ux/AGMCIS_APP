"""The wire format, exactly as the organiser's host speaks it.

    OBS  host -> player   208 bytes   26 little-endian doubles
    CMD  player -> host    30 bytes   5 little-endian floats + b"PLAYER_CMD"

Both sizes are checked rather than trusted. The reference client drops an OBS
whose length is not 208, and the host checks the trailer on every CMD, so a
packet that is one field short is not a degraded command — it is no command at
all, and the aircraft keeps flying on whatever it was last told.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum

import numpy as np

# 26 doubles: 20 ownship, then 6 for the target.
OBS_STRUCT = struct.Struct("<26d")
OBS_PACKET_BYTES = OBS_STRUCT.size  # 208

# roll, pitch, yaw, throttle, player_state, then the trailer.
CMD_STRUCT = struct.Struct("<fffff10s")
CMD_PACKET_BYTES = CMD_STRUCT.size  # 30
PLAYER_CMD_TRAILER = b"PLAYER_CMD"

#: Field order of the OBS packet, from 表 1 of NCSIST-AIPilot-01.
OBS_FIELDS: tuple[str, ...] = (
    "own_lat_deg",
    "own_lon_deg",
    "own_alt_ft",
    "own_roll_deg",
    "own_pitch_deg",
    "own_yaw_deg",
    "own_vn_fps",
    "own_ve_fps",
    "own_vd_fps",
    "own_p_radps",
    "own_q_radps",
    "own_r_radps",
    "own_vc_fps",
    "own_vt_fps",
    "own_g_acc",
    "own_u_fps",
    "own_v_fps",
    "own_w_fps",
    "own_alpha_deg",
    "own_beta_deg",
    "enemy_lat_deg",
    "enemy_lon_deg",
    "enemy_alt_ft",
    "enemy_vn_fps",
    "enemy_ve_fps",
    "enemy_vd_fps",
)


class PlayerState(IntEnum):
    """The handshake value the host waits on before it will start a round."""

    NOT_READY = 0
    INITIALISED = 1
    CONNECTED = 2


class ProtocolError(ValueError):
    """A packet that cannot be what it claims to be."""


@dataclass(frozen=True)
class Command:
    """What the player sends back, in the organiser's own channel order.

    The names are the ones the reference client prints — roll/pitch/yaw — and
    they carry aileron/elevator/rudder deflection commands, normalised.
    """

    roll: float
    pitch: float
    yaw: float
    throttle: float
    player_state: PlayerState = PlayerState.NOT_READY


def decode_observation(payload: bytes) -> np.ndarray:
    """26 doubles from one datagram, as float64.

    float64 on purpose. The reference client casts to float32 here, and the two
    positions it then subtracts differ by a few hundred metres out of a six
    million metre radius, so the cast costs about 0.2 m of resolution. That is
    invisible in the distance and loud in the closure rate, which is a distance
    difference multiplied by 60.
    """
    if len(payload) != OBS_PACKET_BYTES:
        raise ProtocolError(f"an OBS packet is {OBS_PACKET_BYTES} bytes, got {len(payload)}")
    return np.array(OBS_STRUCT.unpack(payload), dtype=np.float64)


def encode_command(command: Command) -> bytes:
    """One CMD datagram, trailer included.

    The floats are cast explicitly: the host reads five 4-byte floats, and a
    numpy scalar that struct cannot pack would fail here rather than on the day.
    """
    payload = CMD_STRUCT.pack(
        float(command.roll),
        float(command.pitch),
        float(command.yaw),
        float(command.throttle),
        float(int(command.player_state)),
        PLAYER_CMD_TRAILER,
    )
    assert len(payload) == CMD_PACKET_BYTES  # the host counts bytes before it reads them
    return payload


def decode_command(payload: bytes) -> Command:
    """Read a CMD packet back. For tests and for the self-test host."""
    if len(payload) != CMD_PACKET_BYTES:
        raise ProtocolError(f"a CMD packet is {CMD_PACKET_BYTES} bytes, got {len(payload)}")
    roll, pitch, yaw, throttle, state, trailer = CMD_STRUCT.unpack(payload)
    if trailer != PLAYER_CMD_TRAILER:
        raise ProtocolError(f"trailer is {trailer!r}, expected {PLAYER_CMD_TRAILER!r}")
    return Command(
        roll=roll,
        pitch=pitch,
        yaw=yaw,
        throttle=throttle,
        player_state=PlayerState(int(state)),
    )
