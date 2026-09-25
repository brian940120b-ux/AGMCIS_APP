"""The 2026 AI 飛行員擂台賽 competition layer (NCSIST-AIPilot).

Everything the organiser's interface fixes lives here, and lives here **once**.

The reference package ships the same logic twice — in the training environment
(`envs/jsbsimEnv/jsbsimEnv.py`) and again in the competition-day client
(`player1_Loadmodel.py`) — and the two copies have drifted apart. Reading them
side by side turns up differences that a policy pays for on the day:

* the elevator is limited to 0.4 above Mach 0.8 in the client and not limited at
  all in the trainer, so a policy meets a control authority it never trained on;
* "Mach" is JSBSim's true Mach in the trainer and true airspeed over a fixed
  340 m/s in the client, so the two do not even agree on when that limit bites;
* the client rounds the received doubles to float32 before differencing
  positions, which the trainer does not, adding roughly +/-12 m/s of noise to a
  closure rate the trainer computes exactly;
* the client's `prev_distance` and `last_actual_action` are module globals with
  no reset, so the first frame of every round after the first is computed from
  the previous round's geometry.

None of those are subtle once seen, and all four exist only because there are
two copies. So there is one copy here: `state.encode` and `action.shape_command`
are called by the training environment and by the UDP client alike, and the
tests assert that what one produces the other produces.
"""

from competition.action import JoystickState, shape_command
from competition.protocol import (
    CMD_PACKET_BYTES,
    OBS_PACKET_BYTES,
    PLAYER_CMD_TRAILER,
    Command,
    PlayerState,
    decode_observation,
    encode_command,
)
from competition.state import STATE_SIZE, StateEncoder, Telemetry

__all__ = [
    "CMD_PACKET_BYTES",
    "OBS_PACKET_BYTES",
    "PLAYER_CMD_TRAILER",
    "STATE_SIZE",
    "Command",
    "JoystickState",
    "PlayerState",
    "StateEncoder",
    "Telemetry",
    "decode_observation",
    "encode_command",
    "shape_command",
]
