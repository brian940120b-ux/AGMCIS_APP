"""The competition client: one OBS in, one CMD out, for five minutes at 60 Hz.

Split in two on purpose. `CompetitionClient` is the whole decision — round
tracking, the handshake, the policy, the stick — and touches no socket, so the
tests fly complete rounds without one. `serve` is the socket loop around it.

**Round boundaries.** The reference client has none: its `prev_distance` and
its stick position are module globals that outlive a round, and its packet
counter never restarts, so from round two onwards it reports itself already
connected without ever saying it initialised. The organiser's own notes ask
each entrant to handle this (NCSIST-AIPilot-01, 參賽者注意事項 2) and describe
the signal: between INIT and START the host repeats the initial position
unchanged, and the round begins when that position starts to move. So a run of
byte-identical positions after movement is a new round, and so is a jump no
aircraft could fly, and both reset everything that carries between frames.

**Loss, delay, duplication.** Rule 10 makes these the entrant's problem and
forbids asking the host to pause. There is no sequence number in the ICD and no
timestamp, so they cannot be detected in general — what can be done is to make
each one harmless and counted. A malformed packet is dropped without a reply,
as the reference does. A duplicate arrives as zero closure for that frame,
which is wrong by less than the float32 noise the reference lives with. A gap
overstates closure for one frame in the same way. None of it is allowed to
change the state layout, because the policy was trained on the layout and not
on the network.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from competition.action import JoystickState, shape_command
from competition.protocol import (
    Command,
    PlayerState,
    ProtocolError,
    decode_observation,
    encode_command,
)
from competition.state import StateEncoder, Telemetry
from core.logging_config import get_logger

log = get_logger("competition.client")

#: Frames of a round before the handshake reports a continuous connection.
#: The reference switches at 60, which at 60 Hz is one second.
CONNECTED_AFTER_FRAMES = 60

#: Furthest an aircraft could move in one 1/60 s frame before the only
#: explanation left is that the host restarted the round. An F-16 at Mach 2
#: covers about 11 m; 200 m is 12 km/s and still far below a reset teleport,
#: which moves the aircraft by kilometres.
MAX_PLAUSIBLE_FRAME_MOVE_M = 200.0

#: A policy: normalised state in, four raw channels out.
Policy = Callable[[np.ndarray], np.ndarray]
#: An observer: every accepted frame, for recording. Returns nothing.
Observer = Callable[["Telemetry", "ClientStats"], None]


@dataclass
class ClientStats:
    """What happened on the wire, for the log and for the post-round report."""

    packets_received: int = 0
    packets_malformed: int = 0
    packets_non_finite: int = 0
    frames_this_round: int = 0
    rounds_seen: int = 0
    frozen_frames: int = 0
    teleports_seen: int = 0
    duplicate_positions: int = 0
    worst_decision_s: float = 0.0
    total_decision_s: float = 0.0

    @property
    def mean_decision_s(self) -> float:
        replies = self.packets_received - self.packets_malformed - self.packets_non_finite
        return self.total_decision_s / replies if replies else 0.0

    def as_dict(self) -> dict[str, float | int]:
        return {
            "packets_received": self.packets_received,
            "packets_malformed": self.packets_malformed,
            "packets_non_finite": self.packets_non_finite,
            "frames_this_round": self.frames_this_round,
            "rounds_seen": self.rounds_seen,
            "teleports_seen": self.teleports_seen,
            "duplicate_positions": self.duplicate_positions,
            "worst_decision_ms": round(self.worst_decision_s * 1000, 3),
            "mean_decision_ms": round(self.mean_decision_s * 1000, 3),
        }


def _is_valid_position(telemetry: Telemetry) -> bool:
    """The reference's own test for "initialisation data has arrived"."""
    return -90.0 <= telemetry.own_lat_deg <= 90.0 and -180.0 <= telemetry.own_lon_deg <= 180.0


class CompetitionClient:
    """Everything between receiving an OBS and having a CMD to send.

    Deterministic and socket-free: hand it observations and it hands back
    commands, which is what makes a five-minute round a unit test.
    """

    def __init__(self, policy: Policy, observer: Observer | None = None) -> None:
        self.policy = policy
        #: Called with every accepted frame, after the round tracking has run.
        #: A tap, not a filter: it cannot change the command, so recording a
        #: session cannot change what that session does.
        self.observer = observer
        self.encoder = StateEncoder()
        self.joystick = JoystickState()
        self.stats = ClientStats()
        self.player_state = PlayerState.NOT_READY
        self._last_own_position: tuple[float, float, float] | None = None
        self._moving = False

    # ------------------------------------------------------------- rounds

    def begin_round(self) -> None:
        """Forget everything that carries between frames.

        Called on the first packet and on every detected round boundary. The
        closure rate, the stick position and the handshake all restart; the
        policy does not, because a policy has no memory to corrupt.
        """
        self.encoder.reset()
        self.joystick.reset()
        self.player_state = PlayerState.NOT_READY
        self.stats.frames_this_round = 0
        self.stats.rounds_seen += 1
        self._moving = False

    def _track_round(self, telemetry: Telemetry) -> None:
        position = (telemetry.own_lat_deg, telemetry.own_lon_deg, telemetry.own_alt_ft)
        previous = self._last_own_position
        self._last_own_position = position

        if previous is None:
            self.begin_round()
            return

        if position == previous:
            self.stats.duplicate_positions += 1
            self.stats.frozen_frames += 1
            if self._moving:
                # Moving, then held exactly still: the host is between rounds.
                log.info(
                    "a new round began",
                    extra={"event": "COMPETITION_ROUND_BEGAN", "reason": "position held"},
                )
                self.begin_round()
            return

        if _frame_move_m(previous, position) > MAX_PLAUSIBLE_FRAME_MOVE_M:
            self.stats.teleports_seen += 1
            log.info(
                "a new round began",
                extra={"event": "COMPETITION_ROUND_BEGAN", "reason": "position jumped"},
            )
            self.begin_round()
            self._moving = False
            return

        self._moving = True

    # ------------------------------------------------------------ handshake

    def _advance_handshake(self, telemetry: Telemetry) -> None:
        """0 until the position is usable, 1 once it is, 2 after a second of it.

        Counted within the round. The reference counts from process start, so
        every round after the first reports 2 immediately and never reports the
        1 the host is waiting to see.
        """
        if self.player_state is PlayerState.NOT_READY:
            if _is_valid_position(telemetry):
                self.player_state = PlayerState.INITIALISED
        elif (
            self.player_state is PlayerState.INITIALISED
            and self.stats.frames_this_round > CONNECTED_AFTER_FRAMES
        ):
            self.player_state = PlayerState.CONNECTED

    # -------------------------------------------------------------- decide

    def on_observation(self, payload: bytes) -> bytes | None:
        """One datagram in, one datagram out, or None if there is nothing to say.

        None means the packet was not an observation. The host is sent nothing
        rather than a command derived from a packet that might be anything,
        which is what the reference does and the safer of the two: a missing
        command leaves the last one standing, an invented one does not.
        """
        self.stats.packets_received += 1
        try:
            obs = decode_observation(payload)
        except ProtocolError as exc:
            self.stats.packets_malformed += 1
            log.warning(
                "dropped a packet that is not an observation",
                extra={"event": "COMPETITION_BAD_PACKET", "detail": str(exc)},
            )
            return None

        if not np.all(np.isfinite(obs)):
            self.stats.packets_non_finite += 1
            log.warning(
                "dropped an observation carrying NaN or infinity",
                extra={"event": "COMPETITION_BAD_PACKET", "detail": "non-finite value"},
            )
            return None

        started = time.perf_counter()
        telemetry = Telemetry.from_observation(obs)
        self._track_round(telemetry)
        self.stats.frames_this_round += 1
        self._advance_handshake(telemetry)

        if self.observer is not None:
            self.observer(telemetry, self.stats)

        state = self.encoder.encode(telemetry)
        raw_action = np.asarray(self.policy(state), dtype=np.float64)
        shaped = shape_command(raw_action, self.joystick, telemetry.reference_mach)

        reply = encode_command(
            Command(
                roll=shaped[0],
                pitch=shaped[1],
                yaw=shaped[2],
                throttle=shaped[3],
                player_state=self.player_state,
            )
        )

        elapsed = time.perf_counter() - started
        self.stats.total_decision_s += elapsed
        self.stats.worst_decision_s = max(self.stats.worst_decision_s, elapsed)
        return reply


def configure_realtime() -> str:
    """Trade a little mean latency for a much shorter tail.

    A policy network of 20 -> 256 -> 256 -> 4 is far too small to pay for the
    synchronisation that multi-threaded BLAS costs, and the cost lands where it
    hurts: in the occasional frame that waits on a thread. Measured over 6,000
    frames with the reference policy on this machine:

        4 threads   mean 0.441 ms   p99.9 5.24 ms   worst 8.07 ms
        1 thread    mean 0.477 ms   p99.9 2.86 ms   worst 5.24 ms

    The frame budget is 16.67 ms and the mean uses 3% of it, so the mean is not
    the number under threat. One measurement each, on a shared machine, so the
    exact figures will differ on the competition laptop — the direction will
    not, because it comes from the shape of the network rather than the host.

    Applied when serving, not at import: training wants every thread it can get.
    """
    try:
        import torch
    except Exception as exc:  # torch is optional; a client can run without it
        return f"torch not configured: {exc}"
    torch.set_num_threads(1)
    return "torch limited to one thread for a shorter latency tail"


def _frame_move_m(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    """Roughly how far the aircraft moved between two frames, in metres."""
    metres_per_degree = 111_320.0
    north = (b[0] - a[0]) * metres_per_degree
    east = (b[1] - a[1]) * metres_per_degree
    up = (b[2] - a[2]) * 0.3048
    return float(np.sqrt(north * north + east * east + up * up))


# ------------------------------------------------------------------ sockets


@dataclass
class Endpoint:
    """Where the host sends to us, and where we send to it."""

    listen_ip: str = "127.0.0.1"
    listen_port: int = 8199
    host_ip: str = "127.0.0.1"
    host_port: int = 8099
    #: Bigger than any packet the host sends, so a long one is seen as long
    #: rather than silently truncated to look like a valid observation.
    buffer_bytes: int = 4096


def serve(
    client: CompetitionClient,
    endpoint: Endpoint | None = None,
    *,
    max_frames: int | None = None,
    timeout_s: float | None = None,
    ready: threading.Event | None = None,
    stop: threading.Event | None = None,
    give_up_after_idle_s: float | None = None,
) -> ClientStats:
    """Receive, decide, reply, until stopped.

    `ready` is set once the socket is bound. Without it there is no way to know
    when this has started listening, and a host that sends into the gap gets no
    answer — on the day that gap is between launching the client and pressing
    INIT, and nobody would notice; in a test it is a race that fails sometimes.

    Silence is not a reason to stop. `timeout_s` is how often the socket wakes
    up to check whether it has been asked to stop, and nothing more:
    the operator has to launch the host, press INIT, wait for both players to
    initialise and then press START, which takes as long as it takes. An earlier
    version treated one socket timeout as the end of the session and gave up
    after a minute of quiet — before the host had sent its first packet.
    `give_up_after_idle_s` restores that behaviour for a caller that wants it.

    `max_frames` and `stop` exist so a test or a supervisor can bound the loop;
    on the day both are None and the loop ends when the operator ends it.
    """
    endpoint = endpoint or Endpoint()
    log.info(
        "preparing for real-time inference",
        extra={"event": "COMPETITION_REALTIME_SETUP", "detail": configure_realtime()},
    )
    inbound = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    outbound = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    inbound.bind((endpoint.listen_ip, endpoint.listen_port))
    if timeout_s is not None:
        inbound.settimeout(timeout_s)
    if ready is not None:
        ready.set()

    log.info(
        "competition client listening",
        extra={
            "event": "COMPETITION_CLIENT_STARTED",
            "listen": f"{endpoint.listen_ip}:{endpoint.listen_port}",
            "host": f"{endpoint.host_ip}:{endpoint.host_port}",
        },
    )
    last_packet_at = time.monotonic()
    try:
        while max_frames is None or client.stats.packets_received < max_frames:
            if stop is not None and stop.is_set():
                break
            try:
                payload, _ = inbound.recvfrom(endpoint.buffer_bytes)
            except TimeoutError:
                idle_s = time.monotonic() - last_packet_at
                if give_up_after_idle_s is not None and idle_s > give_up_after_idle_s:
                    log.info(
                        "no packets for a while, stopping",
                        extra={"event": "COMPETITION_CLIENT_IDLE", "idle_s": round(idle_s, 1)},
                    )
                    break
                continue
            last_packet_at = time.monotonic()
            reply = client.on_observation(payload)
            if reply is not None:
                outbound.sendto(reply, (endpoint.host_ip, endpoint.host_port))
    finally:
        inbound.close()
        outbound.close()
        log.info(
            "competition client stopped",
            extra={"event": "COMPETITION_CLIENT_STOPPED", **client.stats.as_dict()},
        )
    return client.stats
