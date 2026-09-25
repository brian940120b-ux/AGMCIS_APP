"""The competition client, flown through whole rounds (COMP PHASE 2).

The client is socket-free by design, so these are not mocks of a round: they
are rounds. A host is simulated frame by frame — the INIT hold, the start of
motion, five minutes of flying, the stop, the next INIT — and the client is
asked what it would have sent.

What is being checked is mostly what the reference client does not do. Its
`prev_distance`, its stick position and its packet counter are module globals
that outlive a round, so round two begins with round one's geometry, round
one's stick, and a handshake that claims to be connected without ever having
said it initialised.
"""

from __future__ import annotations

import math
import socket
import struct
import threading
import time

import numpy as np
import pytest

from competition.client import (
    CONNECTED_AFTER_FRAMES,
    MAX_PLAUSIBLE_FRAME_MOVE_M,
    CompetitionClient,
    Endpoint,
    serve,
)
from competition.protocol import (
    CMD_PACKET_BYTES,
    OBS_PACKET_BYTES,
    PlayerState,
    decode_command,
)

METRES_PER_DEGREE = 111_320.0
FRAME_S = 1.0 / 60.0


def level_flight(policy_throttle: float = 0.8):
    """A policy that does nothing, so the test measures the client, not a brain."""

    def policy(_state: np.ndarray) -> np.ndarray:
        return np.array([0.0, 0.0, 0.0, policy_throttle])

    return policy


def observation(
    *,
    own_lat: float,
    own_lon: float = 121.948555,
    own_alt_ft: float = 15_000.0,
    enemy_lat: float,
    enemy_lon: float = 121.948555,
    enemy_alt_ft: float = 15_000.0,
    speed_fps: float = 574.0,
) -> bytes:
    values = np.zeros(26, dtype=np.float64)
    values[0], values[1], values[2] = own_lat, own_lon, own_alt_ft
    values[5] = 0.0
    values[6] = speed_fps
    values[12] = values[13] = speed_fps
    values[15] = speed_fps
    values[20], values[21], values[22] = enemy_lat, enemy_lon, enemy_alt_ft
    values[23] = speed_fps
    return struct.pack("<26d", *values)


def fly_a_round(
    client: CompetitionClient,
    *,
    start_lat: float,
    hold_frames: int = 90,
    moving_frames: int = 120,
    closing_m_per_frame: float = 1.0,
) -> list[bytes | None]:
    """The host's own sequence: hold the initial position, then start moving.

    Between INIT and START the host repeats the initial position unchanged.
    That hold, and its end, is the only round boundary the ICD offers.
    """
    replies: list[bytes | None] = []
    enemy_lat = start_lat + 900.0 / METRES_PER_DEGREE
    for _ in range(hold_frames):
        replies.append(client.on_observation(observation(own_lat=start_lat, enemy_lat=enemy_lat)))
    own = start_lat
    for _ in range(moving_frames):
        own += closing_m_per_frame / METRES_PER_DEGREE
        replies.append(client.on_observation(observation(own_lat=own, enemy_lat=enemy_lat)))
    return replies


# ------------------------------------------------------------ round tracking


def test_a_held_position_after_movement_is_a_new_round():
    client = CompetitionClient(level_flight())
    fly_a_round(client, start_lat=23.060552)
    assert client.stats.rounds_seen == 1

    fly_a_round(client, start_lat=23.060552, closing_m_per_frame=0.5)
    assert client.stats.rounds_seen == 2, "the second INIT hold is a second round"

    fly_a_round(client, start_lat=23.060552)
    assert client.stats.rounds_seen == 3


def test_a_teleport_is_a_new_round_even_without_a_hold():
    """A host that restarts straight into motion still moves the aircraft."""
    client = CompetitionClient(level_flight())
    enemy = 23.07
    client.on_observation(observation(own_lat=23.0605, enemy_lat=enemy))
    client.on_observation(observation(own_lat=23.0606, enemy_lat=enemy))
    assert client.stats.rounds_seen == 1

    jump_deg = (MAX_PLAUSIBLE_FRAME_MOVE_M * 5) / METRES_PER_DEGREE
    client.on_observation(observation(own_lat=23.0606 + jump_deg, enemy_lat=enemy))
    assert client.stats.rounds_seen == 2
    assert client.stats.teleports_seen == 1


def test_a_new_round_starts_with_no_closure_rate_carried_over():
    """The defect this phase exists for.

    Round one closes on the target; round two starts far apart again. With the
    reference's module global the first frame of round two differences the two,
    and the policy is handed a closure rate of hundreds of metres per second on
    the one frame it has no way to interpret.
    """
    client = CompetitionClient(level_flight())
    fly_a_round(client, start_lat=23.060552, closing_m_per_frame=3.0)

    first_moving_state: list[np.ndarray] = []
    original = client.encoder.encode

    def spy(telemetry):
        state = original(telemetry)
        first_moving_state.append(state.copy())
        return state

    client.encoder.encode = spy  # type: ignore[method-assign]
    fly_a_round(client, start_lat=23.060552, hold_frames=2, moving_frames=2)

    # Frames: two held, then two moving. The first moving frame is index 2.
    closure = [float(s[13]) for s in first_moving_state]
    assert closure[0] == 0.0, "the first frame of a round has nothing to compare against"
    assert closure[1] == 0.0, "still held"
    assert abs(closure[2]) < 0.2, f"first moving frame should be a sane closure, got {closure[2]}"


def test_a_held_position_takes_two_frames_to_be_a_held_position():
    """The detection costs one frame, and it cannot cost fewer.

    A position is not held until it has been seen twice; the first frame of an
    INIT hold is indistinguishable from a small movement. So a round boundary
    is recognised on the second identical frame, which at 60 Hz is 17 ms into a
    hold the host keeps up until both players are ready.
    """
    client = CompetitionClient(level_flight())
    fly_a_round(client, start_lat=23.06, hold_frames=2, moving_frames=3)
    assert client.stats.rounds_seen == 1

    held = observation(own_lat=23.06, enemy_lat=23.07)
    client.on_observation(held)
    assert client.stats.rounds_seen == 1, "one sighting is a move, not a hold"
    client.on_observation(held)
    assert client.stats.rounds_seen == 2, "the second sighting is the hold"


def test_the_stick_returns_to_centre_between_rounds():
    """A rate-limited stick that keeps its position starts round two deflected."""
    client = CompetitionClient(lambda _s: np.array([1.0, 1.0, 0.0, 1.0]))
    fly_a_round(client, start_lat=23.060552, hold_frames=5, moving_frames=200)
    assert client.joystick.last_command[0] > 0.5, "the stick should be deflected by now"

    # Somewhere the aircraft could not have flown to: unambiguously a new round
    # on the first frame, so the stick has taken exactly one step from centre.
    far = 23.10
    client.on_observation(observation(own_lat=far, enemy_lat=far + 900.0 / METRES_PER_DEGREE))
    assert client.stats.rounds_seen == 2
    assert client.joystick.last_command[0] == pytest.approx(0.05, abs=1e-9), (
        "round two starts from centre, plus the one frame of rate-limited travel"
    )


# ---------------------------------------------------------------- handshake


def test_the_handshake_runs_from_zero_again_in_every_round():
    """The host clears both indicators on STOP and waits to see them again."""
    client = CompetitionClient(level_flight())

    first = client.on_observation(observation(own_lat=23.06, enemy_lat=23.07))
    assert decode_command(first).player_state is PlayerState.INITIALISED

    replies = fly_a_round(client, start_lat=23.06, hold_frames=CONNECTED_AFTER_FRAMES + 5, moving_frames=0)
    assert decode_command(replies[-1]).player_state is PlayerState.CONNECTED

    # A new round: back to reporting initialisation, not straight to connected.
    fly_a_round(client, start_lat=23.06, hold_frames=0, moving_frames=3)
    held = observation(own_lat=23.06, enemy_lat=23.07)
    client.on_observation(held)
    reply = client.on_observation(held)
    assert decode_command(reply).player_state is PlayerState.INITIALISED


def test_an_unusable_position_is_not_reported_as_initialised():
    client = CompetitionClient(level_flight())
    reply = client.on_observation(observation(own_lat=999.0, enemy_lat=23.07))
    assert decode_command(reply).player_state is PlayerState.NOT_READY


# ------------------------------------------------- loss, delay, duplication


@pytest.mark.parametrize("size", [0, 1, 207, 209, 4096])
def test_a_packet_that_is_not_an_observation_gets_no_reply(size: int):
    """Silence leaves the last command standing; a guess replaces it."""
    client = CompetitionClient(level_flight())
    assert client.on_observation(b"\x00" * size) is None
    assert client.stats.packets_malformed == 1


def test_a_packet_carrying_nan_gets_no_reply():
    """The reference does not check. One NaN reaches the policy and stays there."""
    client = CompetitionClient(level_flight())
    values = np.zeros(26, dtype=np.float64)
    values[0] = 23.06
    values[13] = math.nan
    assert client.on_observation(struct.pack("<26d", *values)) is None
    assert client.stats.packets_non_finite == 1


def test_a_duplicated_frame_is_answered_and_counted():
    """Rule 10 makes duplicates ours to absorb, and the host still wants a reply."""
    client = CompetitionClient(level_flight())
    packet = observation(own_lat=23.06, enemy_lat=23.07)
    assert client.on_observation(packet) is not None
    assert client.on_observation(packet) is not None
    assert client.stats.duplicate_positions == 1


def test_every_valid_observation_is_answered_exactly_once():
    client = CompetitionClient(level_flight())
    replies = fly_a_round(client, start_lat=23.06, hold_frames=30, moving_frames=300)
    assert len(replies) == 330
    assert all(r is not None and len(r) == CMD_PACKET_BYTES for r in replies)


# ------------------------------------------------------------------ timing


def test_a_decision_fits_inside_a_frame():
    """16.67 ms per frame at 60 Hz, and the policy here is the cheap part."""
    client = CompetitionClient(level_flight())
    fly_a_round(client, start_lat=23.06, hold_frames=60, moving_frames=600)
    assert client.stats.worst_decision_s < FRAME_S, (
        f"worst decision took {client.stats.worst_decision_s * 1000:.2f} ms"
    )
    assert client.stats.mean_decision_s < FRAME_S / 10


# ------------------------------------------------------------------ sockets


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_the_socket_loop_answers_a_real_host_on_the_loopback():
    """The only test here that uses the network stack, so the packing is real."""
    listen_port = _free_udp_port()
    host_port = _free_udp_port()

    host = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    host.bind(("127.0.0.1", host_port))
    host.settimeout(5.0)

    client = CompetitionClient(level_flight())
    endpoint = Endpoint(
        listen_ip="127.0.0.1",
        listen_port=listen_port,
        host_ip="127.0.0.1",
        host_port=host_port,
    )
    listening = threading.Event()
    worker = threading.Thread(
        target=serve,
        args=(client, endpoint),
        kwargs={"max_frames": 3, "timeout_s": 5.0, "ready": listening},
    )
    worker.start()
    try:
        assert listening.wait(timeout=5.0), "the client never bound its socket"
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        received = []
        for index in range(3):
            packet = observation(own_lat=23.06 + index * 1e-5, enemy_lat=23.07)
            assert len(packet) == OBS_PACKET_BYTES
            sender.sendto(packet, ("127.0.0.1", listen_port))
            reply, _ = host.recvfrom(4096)
            received.append(decode_command(reply))
        sender.close()
    finally:
        worker.join(timeout=10)
        host.close()

    assert len(received) == 3
    assert all(command.player_state is PlayerState.INITIALISED for command in received)
    assert client.stats.packets_received == 3


def test_realtime_setup_reports_what_it_did_and_never_raises():
    """A latency tweak must not be the thing that stops the client starting."""
    from competition.client import configure_realtime

    detail = configure_realtime()
    assert isinstance(detail, str) and detail

    torch = pytest.importorskip("torch")
    assert torch.get_num_threads() == 1


def test_silence_is_not_a_reason_to_stop_listening():
    """The host is started by hand, and that takes longer than any timeout.

    An earlier version treated one socket timeout as the end of the session and
    gave up after a minute of quiet — which is less than it takes to launch the
    host, press INIT, wait for both players to initialise and press START. The
    first real attempt at a probe recorded zero packets for exactly this reason.
    """
    listen_port = _free_udp_port()
    host_port = _free_udp_port()
    host = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    host.bind(("127.0.0.1", host_port))
    host.settimeout(5.0)

    client = CompetitionClient(level_flight())
    endpoint = Endpoint(
        listen_ip="127.0.0.1",
        listen_port=listen_port,
        host_ip="127.0.0.1",
        host_port=host_port,
    )
    listening = threading.Event()
    stop = threading.Event()
    worker = threading.Thread(
        target=serve,
        args=(client, endpoint),
        kwargs={"timeout_s": 0.05, "ready": listening, "stop": stop},
        daemon=True,
    )
    worker.start()
    try:
        assert listening.wait(timeout=5.0)
        # Several socket timeouts pass with nothing arriving.
        time.sleep(0.4)
        assert worker.is_alive(), "it should still be listening"

        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sender.sendto(observation(own_lat=23.06, enemy_lat=23.07), ("127.0.0.1", listen_port))
        reply, _ = host.recvfrom(4096)
        sender.close()
        assert decode_command(reply).player_state is PlayerState.INITIALISED
    finally:
        stop.set()
        worker.join(timeout=5)
        host.close()


def test_a_caller_that_wants_to_give_up_on_silence_still_can():
    listen_port = _free_udp_port()
    client = CompetitionClient(level_flight())
    endpoint = Endpoint(listen_ip="127.0.0.1", listen_port=listen_port, host_port=_free_udp_port())
    started = time.monotonic()
    serve(client, endpoint, timeout_s=0.05, give_up_after_idle_s=0.2)
    assert time.monotonic() - started < 3.0
