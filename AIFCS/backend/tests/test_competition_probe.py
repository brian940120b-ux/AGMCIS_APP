"""The host probe, driven by a host (COMP PHASE 6).

The real host is a Windows executable and is not run here. What is run is a
stand-in built from `CompetitionRound` — two JSBSim F-16s, real OBS packets over
a real socket, real CMD packets coming back — doing the one thing about the real
host's behaviour that matters to the probe: holding the initial position between
INIT and START, then letting it move.

That is enough to test what the probe claims. Whether the real host behaves the
same way is exactly what the probe exists to find out, and no test here can
answer it.
"""

from __future__ import annotations

import socket
import struct
import threading
import time

import numpy as np
import pytest

from competition.client import CompetitionClient
from competition.probe import KNOTS_PER_FPS, LevelPolicy, Recorder, _precompensate, analyse
from competition.protocol import decode_command

pytest.importorskip("jsbsim")

from competition.environment import CompetitionRound, EnvConfig


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


# ------------------------------------------------------------- the analysis


def _frame(**overrides):
    frame = {
        "wall_clock": 0.0,
        "packet": 1,
        "round": 1,
        "frame_in_round": 1,
        "lat": 23.06,
        "lon": 121.95,
        "alt_ft": 15_000.0,
        "vc_fps": 340.0 / KNOTS_PER_FPS,
        "vt_fps": 419.0 / KNOTS_PER_FPS,
        "g": 1.0,
        "roll": 0.0,
        "pitch": 0.0,
        "yaw": 0.0,
        "alpha": 2.0,
        "enemy_lat": 23.07,
        "enemy_lon": 121.95,
        "enemy_alt_ft": 15_000.0,
    }
    frame.update(overrides)
    return frame


def test_the_analysis_recognises_the_published_initial_speed():
    report = analyse([_frame()])
    assert "340 KCAS" in report["speed_verdict"]
    assert report["initial"]["vc_kts"] == pytest.approx(340.0, abs=1.0)


def test_the_analysis_recognises_the_references_slow_start():
    """234 KCAS means the host has the ordering defect and training must match."""
    slow = _frame(vc_fps=234.0 / KNOTS_PER_FPS, vt_fps=292.0 / KNOTS_PER_FPS)
    report = analyse([slow])
    assert "234 KCAS" in report["speed_verdict"]
    assert "--reference-speed-order" in report["speed_verdict"]


def test_the_analysis_refuses_to_guess_at_a_third_answer():
    report = analyse([_frame(vc_fps=500.0 / KNOTS_PER_FPS)])
    assert "neither" in report["speed_verdict"]


def test_an_empty_recording_says_what_to_check():
    report = analyse([])
    assert report["frames"] == 0
    assert "firewall" in report["verdict"]


def test_the_analysis_counts_the_frames_the_host_held_still():
    held = [_frame() for _ in range(10)]
    moving = [_frame(lat=23.06 + i * 1e-5) for i in range(1, 11)]
    report = analyse(held + moving)
    assert report["frames_with_position_held"] == 9


# ------------------------------------------------------------ the stick maths


@pytest.mark.parametrize("desired", [0.05, 0.2, -0.3, 0.5])
def test_precompensation_survives_the_cube_the_stick_applies(desired: float):
    """A correction of 0.05 arrives as 0.000125 unless its cube root is asked for.

    This is the same defect that flew the first opponent into the ground at 21
    seconds, in the one other place that has to know about it.
    """
    from competition.action import JoystickState, shape_command

    joystick = JoystickState()
    raw = _precompensate(desired, 1)
    for _ in range(400):  # long enough for the rate limit to arrive
        shaped = shape_command(np.array([0.0, raw, 0.0, 0.8]), joystick, 0.5)
    assert shaped[1] == pytest.approx(desired, abs=0.01)


def test_the_level_policy_asks_for_something_the_stick_can_use():
    policy = LevelPolicy()
    state = np.zeros(20, dtype=np.float32)
    state[6] = 1.0  # cos(roll) = 1
    state[8] = 1.0  # cos(pitch) = 1
    state[11] = 3.0  # 15,000 m — the first frame sets the target
    command = policy(state)
    assert command.shape == (4,)
    assert np.all(np.isfinite(command))
    assert abs(command[1]) > 0.05, "a cubed channel needs more than the deadband"


# ------------------------------------------------------- against a real host


class LoopbackHost:
    """A stand-in host: real JSBSim, real packets, INIT hold then motion."""

    def __init__(self, player_port: int, hold_frames: int = 90) -> None:
        self.round = CompetitionRound(EnvConfig(), seed=5)
        self.player_port = player_port
        self.hold_frames = hold_frames
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.settimeout(2.0)
        self.port = int(self.socket.getsockname()[1])
        self.sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.states_seen: list[int] = []
        self.stop = threading.Event()

    def serve_round(self, moving_frames: int) -> None:
        held = struct.pack("<26d", *self.round.telemetry().as_observation())
        for _ in range(self.hold_frames):
            self._exchange(held)
            if self.stop.is_set():
                return
        for _ in range(moving_frames):
            command = self._exchange(struct.pack("<26d", *self.round.telemetry().as_observation()))
            action = (
                np.array([command.roll, command.pitch, command.yaw, command.throttle])
                if command
                else np.array([0.0, 0.0, 0.0, 0.8])
            )
            self.round.step(action)
            if self.stop.is_set():
                return

    def _exchange(self, payload: bytes):
        self.sender.sendto(payload, ("127.0.0.1", self.player_port))
        try:
            reply, _ = self.socket.recvfrom(4096)
        except TimeoutError:
            return None
        command = decode_command(reply)
        self.states_seen.append(int(command.player_state))
        return command

    def close(self) -> None:
        self.socket.close()
        self.sender.close()


def test_the_probe_flies_a_round_against_a_host_and_reports_on_it():
    """End to end: OBS in over a socket, CMD out, two rounds, one report."""
    player_port = _free_port()
    recorder = Recorder()
    client = CompetitionClient(LevelPolicy(), observer=recorder)

    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listener.bind(("127.0.0.1", player_port))
    listener.settimeout(5.0)
    replier = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    host = LoopbackHost(player_port, hold_frames=30)

    def player() -> None:
        deadline = time.time() + 30.0
        while time.time() < deadline and not host.stop.is_set():
            try:
                payload, _ = listener.recvfrom(4096)
            except TimeoutError:
                return
            reply = client.on_observation(payload)
            if reply is not None:
                replier.sendto(reply, ("127.0.0.1", host.port))

    worker = threading.Thread(target=player, daemon=True)
    worker.start()
    try:
        host.serve_round(moving_frames=300)
        host.round.reset(seed=6)
        host.serve_round(moving_frames=120)
    finally:
        host.stop.set()
        worker.join(timeout=10)
        listener.close()
        replier.close()
        host.close()

    report = analyse(recorder.frames)
    assert report["frames"] > 400
    assert report["rounds_detected"] == 2, "the INIT hold should mark the second round"
    assert report["frames_with_position_held"] >= 50
    assert report["initial"]["separation_ft"] > 0
    # The handshake has to have gone 1 then 2, or the host would never start.
    assert 1 in host.states_seen and 2 in host.states_seen
