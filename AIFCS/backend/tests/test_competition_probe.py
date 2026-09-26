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

import itertools
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
    assert report["initial"]["horizontal_separation_ft"] > 0
    # The handshake has to have gone 1 then 2, or the host would never start.
    assert 1 in host.states_seen and 2 in host.states_seen


def test_the_self_test_passes_against_itself():
    """It has to be trustworthy, since it is what a silent session is judged by."""
    from competition.probe import parse_args, selftest

    args = parse_args(["--listen-port", str(_free_port()), "--host-port", str(_free_port())])
    assert selftest(args) == 0


def test_the_self_test_fails_when_the_host_port_is_taken():
    """The one local cause it can distinguish: something else holds the port."""
    from competition.probe import parse_args, selftest

    host_port = _free_port()
    squatter = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    squatter.bind(("127.0.0.1", host_port))
    try:
        args = parse_args(["--listen-port", str(_free_port()), "--host-port", str(host_port)])
        assert selftest(args) == 1
    finally:
        squatter.close()


def test_the_separation_accounts_for_the_cosine_of_the_latitude():
    """A degree of longitude shrinks with latitude, and the first version forgot.

    The numbers are the real host's first frame, 2026-09-25: two F-16s at
    25.328 N, at the same altitude. With the cosine the separation is 3,295 ft;
    without it, 3,604 — which was reported, and looked like the published
    3,000 / 6,000 / 9,000 being wrong rather than this being wrong.
    """
    from competition.probe import _separation

    frame = _frame(
        lat=25.32831559268268,
        lon=121.21688960512867,
        alt_ft=19116.00001178682,
        enemy_lat=25.32515018725573,
        enemy_lon=121.20754202869814,
        enemy_alt_ft=19116.00002093613,
    )
    horizontal_ft, vertical_ft, slant_ft = _separation(frame)

    assert horizontal_ft == pytest.approx(3295.0, abs=2.0)
    assert vertical_ft == pytest.approx(0.0, abs=0.001)
    assert slant_ft == pytest.approx(horizontal_ft, abs=0.001)
    # Without the cosine it comes out here, which is what was reported.
    assert horizontal_ft < 3604.0


def test_the_host_starts_both_aircraft_at_the_same_altitude():
    """Measured: they matched to 1e-5 ft, which two random draws never do."""
    from competition.probe import _separation

    _, vertical_ft, _ = _separation(_frame(alt_ft=19116.00001178682, enemy_alt_ft=19116.00002093613))
    assert abs(vertical_ft) < 0.001


# --------------------------------------------------------- does the host trim?


def _flying(altitudes: list[float]) -> list[dict[str, object]]:
    return [{"round": 1, "alt_ft": ft} for ft in altitudes]


def test_a_diving_recording_says_the_host_does_not_trim():
    """Ours loses about 390 ft a second from a centred stick. A host that does
    the same means every baseline measured against our environment stands."""
    from competition.probe import trim_verdict

    seconds = 10
    verdict = trim_verdict(_flying([19_000.0 - 390.0 * (i / 60.0) for i in range(seconds * 60)]))
    assert "does NOT trim" in verdict


def test_a_level_recording_says_the_host_trims_and_calls_for_a_remeasure():
    """The answer that invalidates work, so it has to say so rather than read
    as a clean pass."""
    from competition.probe import trim_verdict

    verdict = trim_verdict(_flying([19_000.0 + (i % 7) * 0.5 for i in range(10 * 60)]))
    assert "DOES trim" in verdict
    assert "measured again" in verdict


def test_a_recording_with_no_flying_frames_says_so_rather_than_guessing():
    """Forgetting to press START produces frames, all of them held. Reporting
    "does not trim" from those would be the worst possible answer."""
    from competition.probe import trim_verdict

    assert "not enough flying frames" in trim_verdict([{"round": 0, "alt_ft": 19_000.0}] * 600)


def test_the_neutral_flag_actually_changes_what_is_flown():
    """The wiring, not the policy. A --neutral run that flew the altitude-holding
    autopilot would answer the trim question with the autopilot's own behaviour,
    and look like a clean result while doing it."""
    from competition.probe import LevelPolicy, NeutralPolicy, parse_args, policy_for

    assert isinstance(policy_for(parse_args(["--neutral"])), NeutralPolicy)
    assert isinstance(policy_for(parse_args([])), LevelPolicy)


def test_a_centred_stick_is_centred_on_all_three_axes():
    """Zero on the throttle would be an order to close it, not "do nothing" —
    the mistake the evaluator's neutral baseline made once already."""
    import numpy as np

    from competition.probe import NeutralPolicy

    command = NeutralPolicy()(np.zeros(20))
    assert list(command[:3]) == [0.0, 0.0, 0.0]
    assert command[3] == pytest.approx(0.8), "the throttle a round starts at"


# ------------------------------------------- is it even the same aeroplane?


def _host_frame(i: int, alt_ft: float, kts: float, from_ft: float, pitch: float) -> dict[str, object]:
    return {
        "round": 1,
        "frame_in_round": i,
        "lat": 0.0,
        "lon": 0.0,
        "alt_ft": alt_ft,
        "vc_fps": kts / 0.5924838,
        "vt_fps": kts / 0.5924838,
        "yaw": 1.0,
        "pitch": pitch,
        "alpha": pitch,
        "enemy_lat": 0.012,
        "enemy_lon": 0.0,
        "enemy_alt_ft": from_ft,
    }


def _dive(seconds: float, from_ft: float, to_ft: float) -> list[dict[str, object]]:
    """A straight-line fall. Not what any aeroplane does, but it diverges from
    ours throughout, which is the "different aeroplane" case."""
    total = int(seconds * 60)
    return [
        _host_frame(i, from_ft - (from_ft - to_ft) * (i / total), 339.9, from_ft, 0.0) for i in range(total)
    ]


def _host_shaped_dive() -> list[dict[str, object]]:
    """The real 2026-09-26 trace, interpolated between its decade marks.

    The shape is the finding: 129 ft lost in the first ten seconds against our
    1,007, and then both descending alike.
    """
    altitudes = [
        (0, 18084.0),
        (10, 17955.0),
        (20, 16160.0),
        (30, 12891.0),
        (40, 9182.0),
        (50, 5616.0),
        (67.5, 94.3),
    ]
    speeds = [(0, 339.9), (10, 371.0), (20, 473.0), (30, 568.0), (40, 626.0), (50, 655.0), (67.5, 673.8)]

    def at(table, t):
        for (t0, v0), (t1, v1) in itertools.pairwise(table):
            if t0 <= t <= t1:
                return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
        return table[-1][1]

    total = int(67.5 * 60)
    return [_host_frame(i, at(altitudes, i / 60), at(speeds, i / 60), 18084.0, 6.5) for i in range(total)]


def test_it_refuses_to_compare_a_recording_too_short_to_mean_anything():
    """Four seconds of dive says nothing about an aeroplane, and answering
    anyway would be worse than declining."""
    from competition.probe import replay_locally

    result = replay_locally(_dive(4.0, 18_084.0, 17_000.0))
    assert "at least 10 s" in result["verdict"]
    assert "samples" not in result


def test_a_shallower_host_dive_is_reported_as_a_different_aeroplane():
    """The real recording: 18,084 ft to 94 ft in 67.5 s, where ours takes 58.2.

    Our plant reaching the ground sooner on the same centred stick is the whole
    finding, and the verdict has to name it rather than report a pass with a
    number buried underneath.
    """
    from competition.probe import replay_locally

    result = replay_locally(_dive(67.5, 18_084.0, 94.3))
    assert "different aeroplane" in result["verdict"]
    assert result["samples"][0]["difference_ft"] == 0.0, "both start where the host started"
    assert result["samples"][-1]["difference_ft"] < 0, "ours is lower by the end"


def test_a_mangled_windows_path_is_explained_not_traced(tmp_path, capsys, monkeypatch):
    """Backslashes are escape characters in the shell this is run from, so a
    pasted `data\\probe\\probe-....jsonl` arrives as one run-on word and the
    traceback names a file nobody typed. That is a confusing way to learn it."""
    from competition.probe import compare_recording

    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "data" / "probe"
    folder.mkdir(parents=True)
    (folder / "probe-20260926-121044.jsonl").write_text("", encoding="utf-8")

    assert compare_recording("dataprobeprobe-20260926-121044.jsonl") == 1
    printed = capsys.readouterr().err
    assert "forward slashes" in printed
    assert "data/probe/probe-20260926-121044.jsonl" in printed, "and name what is there"


def test_a_gap_that_opens_and_then_stops_growing_is_an_attitude_not_an_aeroplane():
    """The finding the real recording forced.

    Reporting "+14%, a different aeroplane" was the wrong conclusion from the
    right number: the host loses 129 ft in its first ten seconds and ours
    1,007, and thereafter the two descend within 10% of each other. A
    difference created at the start and not sustained is a starting attitude.
    Ours sets ic/theta-deg = 0, and level flight at 18,084 ft and 340 KCAS
    needs a positive angle of attack.
    """
    from competition.probe import replay_locally

    result = replay_locally(_host_shaped_dive())
    assert "different opening attitude" in result["verdict"]
    assert "different aeroplane" not in result["verdict"]
    assert result["host_opening"]["ours_pitch_deg"] == 0.0


def test_the_recording_own_opening_attitude_is_reported_not_inferred():
    """A pitch fitted to the altitude trace is a guess; the recording carries
    the host's own reading, so report that and let the fit be checked."""
    from competition.probe import replay_locally

    result = replay_locally(_host_shaped_dive())
    assert result["host_opening"]["pitch_deg"] == 6.5
    assert result["host_opening"]["alpha_deg"] == 6.5
