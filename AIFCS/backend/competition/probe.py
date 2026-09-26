"""Connect to the real host, fly straight, record everything, and report.

Three questions have been carried since PHASE 4 and none of them can be settled
by reading anything. All three are answered by what the host actually sends:

**What speed does a round start at?** The published rules say 340 knots. The
reference environment sets `ic/vc-kts` before `ic/h-sl-ft`, which resolves the
calibrated airspeed at sea level and leaves the aircraft 106 knots slow — 234
KCAS and Mach 0.466 instead of 340 and 0.669. If the host has that ordering too,
then 234 is the competition's real initial speed and training at 340 is the
mismatch. The OBS packet carries `vc-fps` and `vt-fps` in fields 13 and 14, so
the first frame of the first round says which it is, exactly.

**Which F-16 does the host fly?** The package ships an `aircraft/f16` whose
engine differs from the one pip installs, and the reference training loads the
pip one. The two differ in bypass ratio, bleed and idle N1/N2, so they spool
differently: hold the throttle at a step and the airspeed trace separates. This
records the trace; matching it is done offline against both.

**Is the round boundary what we think it is?** PHASE 2 detects a new round from
a position held unchanged after movement, inferred from the organiser's note
that a round begins when the position starts to change. This records the frames
either side of INIT and START so the inference can be checked against what the
host does.

ANSWERED, 2026-09-25, against the real host over two rounds: it starts at 339.9
KCAS and Mach 0.673, so the published figure is the real one and the reference
package's ordering defect is the reference's alone; and 6,729 frames repeated
the previous position exactly, from which both rounds were detected, so the
inferred boundary is the host's actual behaviour. Decision latency on the
competition laptop was 0.20 ms mean and 1.11 ms worst against a 16.67 ms budget,
with no malformed packets.

The probe replies to every frame, because a host that gets no command sees a
player that has not initialised. It flies wings-level and holds its starting
altitude — enough to keep the aircraft out of the ground for five minutes
without pretending to be a competitor.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import struct
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from competition.action import DEADBANDS, EXPONENTS
from competition.client import ClientStats, CompetitionClient, Endpoint, serve
from competition.state import FT_TO_M, Telemetry

KNOTS_PER_FPS = 1.0 / 1.68781


def _precompensate(desired: float, axis: int) -> float:
    """Undo the stick's exponential curve, so a small correction stays small.

    `shape_command` cubes the elevator and rudder channels, so a command of 0.05
    arrives as 0.000125. Anything that wants a particular surface deflection has
    to ask for its cube root. Getting this wrong is what flew the first opponent
    into the ground at 21 seconds.
    """
    if abs(desired) < 1e-9:
        return 0.0
    magnitude = abs(desired) ** (1.0 / EXPONENTS[axis])
    magnitude = max(magnitude, DEADBANDS[axis] * 1.01)
    return math.copysign(magnitude, desired)


@dataclass
class LevelPolicy:
    """Wings level, hold the altitude of the first frame. Not a competitor.

    Reads what it needs out of the normalised state rather than taking
    telemetry, because that is the interface a policy has and the probe should
    fly through the same path a policy will.
    """

    target_altitude_m: float | None = None

    def __call__(self, state: np.ndarray) -> np.ndarray:
        roll_rad = math.atan2(float(state[5]), float(state[6]))
        pitch_rad = math.atan2(float(state[7]), float(state[8]))
        altitude_m = float(state[11]) * 5000.0
        if self.target_altitude_m is None:
            self.target_altitude_m = altitude_m

        altitude_error_m = self.target_altitude_m - altitude_m
        target_pitch_rad = math.radians(max(-5.0, min(8.0, altitude_error_m / FT_TO_M * 0.01)))
        elevator = -max(-0.5, min(0.5, (target_pitch_rad - pitch_rad) * 1.5 + 0.05))
        aileron = max(-0.5, min(0.5, -roll_rad * 0.8))
        return np.array([aileron, _precompensate(elevator, 1), 0.0, 0.8], dtype=np.float64)


@dataclass
class NeutralPolicy:
    """Stick centred, throttle held. The one question LevelPolicy cannot answer.

    Everything measured so far rests on a single claim: a centred stick flies
    this aircraft into the ground in 49 seconds, because the round starts
    untrimmed. That is true of *our* environment, and the whole case for the
    ground-avoidance floor rests on it being true of the host as well.

    LevelPolicy cannot test it. It is an altitude-holding autopilot, so it
    corrects exactly the drift the question is about, and a host that trims and
    a host that does not would both come back flying level.

    The throttle is 0.8 because that is the reference reset value and the OBS
    packet carries no throttle to read the real one from. If the host starts it
    elsewhere this is a small step at frame one, not a hold, and the altitude
    trace still answers the question.
    """

    throttle: float = 0.8

    def __call__(self, state: np.ndarray) -> np.ndarray:
        return np.array([0.0, 0.0, 0.0, self.throttle], dtype=np.float64)


@dataclass
class Recorder:
    """Every accepted frame, kept in memory and written once at the end."""

    frames: list[dict[str, Any]] = field(default_factory=list)
    limit: int = 200_000

    def __call__(self, telemetry: Telemetry, stats: ClientStats) -> None:
        if len(self.frames) >= self.limit:
            return
        self.frames.append(
            {
                "wall_clock": time.time(),
                "packet": stats.packets_received,
                "round": stats.rounds_seen,
                "frame_in_round": stats.frames_this_round,
                "lat": telemetry.own_lat_deg,
                "lon": telemetry.own_lon_deg,
                "alt_ft": telemetry.own_alt_ft,
                "vc_fps": telemetry.own_vc_fps,
                "vt_fps": telemetry.own_vt_fps,
                "g": telemetry.own_g_acc,
                "roll": telemetry.own_roll_deg,
                "pitch": telemetry.own_pitch_deg,
                "yaw": telemetry.own_yaw_deg,
                "alpha": telemetry.own_alpha_deg,
                "enemy_lat": telemetry.enemy_lat_deg,
                "enemy_lon": telemetry.enemy_lon_deg,
                "enemy_alt_ft": telemetry.enemy_alt_ft,
            }
        )


def _from_first_motion(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop the frames where the host is holding the aircraft still.

    The host freezes both aircraft between INIT and START and keeps sending
    packets while it does, so a round begins with a stretch where the position
    is byte-identical frame to frame. `analyse` already counts them: the
    2026-09-26 recording has 374, which is 6.23 seconds.

    Starting a replay at frame zero flies ours through those 6.23 seconds while
    the host sits still, and then compares t against t+6.23. That is what the
    "+14%, a different aeroplane" verdict was measuring. Aligned on first
    motion the two pitch traces agree to a tenth of a degree at ten and twenty
    seconds, and the aeroplanes turn out to be the same one.
    """
    for index, (previous, current) in enumerate(itertools.pairwise(frames)):
        moved = (previous["lat"], previous["lon"], previous["alt_ft"]) != (
            current["lat"],
            current["lon"],
            current["alt_ft"],
        )
        if moved:
            return frames[index:]
    return frames


def replay_locally(frames: list[dict[str, Any]], throttle: float = 0.8) -> dict[str, Any]:
    """Fly our own plant through the host's recorded dive and compare the traces.

    Only meaningful on a `--neutral` recording, because that is the one case
    where the commands are known without having recorded them: a centred stick
    every frame. Anything else would need the CMD packets too.

    The question this settles is the one the docs have carried unanswered since
    the start — whether the host's F-16 is the one we train on. Measured on the
    2026-09-26 recording: from 18,084 ft the host reaches 94 ft in 67.5 s and
    ours in 58.2 s, 16% quicker. Same direction, same terminal speed to within
    3%, different aeroplane.

    JSBSim is imported here rather than at module scope because the rest of
    this file is the network probe and must run on a machine without it.
    """
    import numpy as np

    from competition.action import JoystickState, shape_command
    from competition.environment import Aircraft, resolve_jsbsim_root

    in_round = [f for f in frames if f["round"] >= 1]
    flying = _from_first_motion(in_round)
    held = len(in_round) - len(flying)
    if len(flying) < 10 * 60:
        return {"verdict": "need at least 10 s of flying frames to compare"}

    first = flying[0]
    root = resolve_jsbsim_root(None)
    own = Aircraft(
        root=root,
        lat_deg=first["lat"],
        lon_deg=first["lon"],
        altitude_ft=first["alt_ft"],
        heading_deg=first["yaw"],
        speed_kcas=first["vc_fps"] * KNOTS_PER_FPS,
    )
    foe = Aircraft(
        root=root,
        lat_deg=first["enemy_lat"],
        lon_deg=first["enemy_lon"],
        altitude_ft=first["enemy_alt_ft"],
        heading_deg=first["yaw"] + 180.0,
        speed_kcas=first["vc_fps"] * KNOTS_PER_FPS,
    )

    stick = JoystickState()
    stick.last_command = np.array([0.0, 0.0, 0.0, throttle], dtype=np.float64)
    centred = np.array([0.0, 0.0, 0.0, throttle], dtype=np.float64)

    samples = []
    for index, host_frame in enumerate(flying):
        ours_ft = float(own.fdm["position/h-sl-ft"])
        if index % (10 * 60) == 0:
            samples.append(
                {
                    "t_s": round(index / 60.0, 1),
                    "host_ft": round(host_frame["alt_ft"], 0),
                    "ours_ft": round(ours_ft, 0),
                    "difference_ft": round(ours_ft - host_frame["alt_ft"], 0),
                    "host_kts": round(host_frame["vc_fps"] * KNOTS_PER_FPS, 0),
                    "ours_kts": round(float(own.fdm["velocities/vc-kts"]), 0),
                    # The discriminator. Both start at pitch 0 — measured, not
                    # assumed — so whatever holds the host's nose up is not its
                    # initial attitude. Watching the two noses is how to find
                    # out what it is.
                    "host_pitch": round(host_frame["pitch"], 1),
                    "ours_pitch": round(float(own.fdm["attitude/theta-deg"]), 1),
                    "host_alpha": round(host_frame["alpha"], 1),
                    "ours_alpha": round(float(own.fdm["aero/alpha-deg"]), 1),
                }
            )
        if ours_ft <= 0.0:
            break
        command = shape_command(centred, stick, own.telemetry_against(foe).reference_mach)
        own.apply(command)
        own.step()
        foe.apply(centred)
        foe.step()

    descended = first["alt_ft"] - flying[-1]["alt_ft"]
    host_rate = descended / (len(flying) / 60.0)
    ours_rate = (first["alt_ft"] - samples[-1]["ours_ft"]) / (samples[-1]["t_s"] or 1.0)
    gap = (ours_rate - host_rate) / host_rate if host_rate else 0.0

    # Where the gap comes from matters more than its size. Two aeroplanes
    # disagree throughout; two starting attitudes disagree at the start and
    # then descend alike, which is what the 2026-09-26 recording shows — the
    # host loses 129 ft in the first ten seconds and ours loses 1,007, and by
    # the third the two rates are within 10%.
    late = samples[-3:] if len(samples) >= 4 else samples[-2:]
    late_host = late[0]["host_ft"] - late[-1]["host_ft"]
    late_ours = late[0]["ours_ft"] - late[-1]["ours_ft"]
    late_gap = (late_ours - late_host) / late_host if late_host else 0.0
    opening = samples[1]["difference_ft"] if len(samples) > 1 else 0.0

    same_start = abs(first["pitch"] - 0.0) < 1.0
    nose = samples[1] if len(samples) > 1 else samples[0]
    nose_gap = nose["ours_pitch"] - nose["host_pitch"]

    if abs(gap) < 0.05:
        verdict = f"our plant matches the host's to {abs(gap):.0%} on descent rate"
    elif abs(late_gap) < 0.15 and same_start:
        verdict = (
            f"same aeroplane, same opening attitude (both pitch "
            f"{first['pitch']:.1f}), but ours drops its nose: {nose['ours_pitch']:+.1f} "
            f"against {nose['host_pitch']:+.1f} degrees at ten seconds, "
            f"{opening:+,.0f} ft apart. Something is holding the host's nose up "
            f"that is not holding ours — elevator trim, CG, or pitching moment. "
            f"They descend within {abs(late_gap):.0%} of each other once both are down"
        )
    elif abs(late_gap) < 0.15:
        verdict = (
            f"same aeroplane, different opening attitude: host starts at pitch "
            f"{first['pitch']:.1f} and ours at 0, {opening:+,.0f} ft apart after "
            f"ten seconds, descending within {abs(late_gap):.0%} by the end"
        )
    else:
        verdict = (
            f"our plant descends {gap:+.0%} against the host's "
            f"({ours_rate:.0f} vs {host_rate:.0f} ft/s) and keeps doing it "
            f"({late_gap:+.0%} over the last stretch) — a different aeroplane, "
            "and a policy is trained on the aeroplane"
        )
    return {
        # How many frames of the round the host spent holding the aircraft
        # still before letting it go. Reported rather than assumed: a shift of
        # 6.2 s aligns the two pitch traces to a tenth of a degree, and 6.2 s
        # is 374 frames, which is what the 2026-09-26 recording counted. That
        # is suggestive, not proof, until this number says the hold was inside
        # the round and this replay skipped it.
        "held_frames_skipped": held,
        "held_seconds_skipped": round(held / 60.0, 2),
        "host_opening": {
            "pitch_deg": round(first["pitch"], 2),
            "alpha_deg": round(first["alpha"], 2),
            "ours_pitch_deg": 0.0,
            "nose_gap_at_10s_deg": round(nose_gap, 1),
        },
        "samples": samples,
        "verdict": verdict,
    }


def trim_verdict(frames: list[dict[str, Any]]) -> str:
    """Does the host hand us a trimmed aircraft, or one that flies itself down?

    Only meaningful on a recording flown with a centred stick (`--neutral`).
    Our environment loses roughly 390 ft a second on average over the first 49
    seconds from 19,000 ft, so the two answers are not close together and no
    fine threshold is needed: 100 ft a second sits an order of magnitude below
    an untrimmed dive and well above the drift of a trimmed aircraft.
    """
    flying = [f for f in frames if f["round"] >= 1]
    if len(flying) < 2 * 60:
        return "not enough flying frames to say — was START pressed?"

    seconds = (len(flying) - 1) / 60.0
    lost_ft = flying[0]["alt_ft"] - flying[-1]["alt_ft"]
    rate = lost_ft / seconds if seconds else 0.0
    measured = f"{lost_ft:+,.0f} ft over {seconds:.0f} s ({rate:+.0f} ft/s)"

    if rate > 100.0:
        return (
            f"host does NOT trim: {measured}. Same as ours, so the floor and "
            "every baseline measured against it stand"
        )
    if rate < -100.0:
        return f"host starts nose-up: {measured}. Unexpected — send this recording back"
    return (
        f"host DOES trim: {measured}. Ours dives instead, so the environment is "
        "wrong and every baseline has to be measured again"
    )


def analyse(frames: list[dict[str, Any]]) -> dict[str, Any]:
    """What the recording says about the three open questions."""
    if not frames:
        return {"frames": 0, "verdict": "no packets arrived — check IP, port and firewall"}

    first = frames[0]
    vc_kts = first["vc_fps"] * KNOTS_PER_FPS
    vt_kts = first["vt_fps"] * KNOTS_PER_FPS

    # Which reading of the initial speed the host agrees with.
    if abs(vc_kts - 340.0) < 15.0:
        speed_verdict = "host starts at 340 KCAS — the published figure, ordering correct"
    elif abs(vc_kts - 234.0) < 25.0:
        speed_verdict = (
            "host starts near 234 KCAS — it has the reference's ordering, "
            "so train with --reference-speed-order"
        )
    else:
        speed_verdict = f"host starts at {vc_kts:.0f} KCAS — neither 340 nor 234, decide by hand"

    # How the host holds position between INIT and START.
    held = 0
    for previous, current in itertools.pairwise(frames):
        if (previous["lat"], previous["lon"], previous["alt_ft"]) == (
            current["lat"],
            current["lon"],
            current["alt_ft"],
        ):
            held += 1

    rounds = max(frame["round"] for frame in frames)
    altitudes = [frame["alt_ft"] for frame in frames]
    speeds = [frame["vc_fps"] * KNOTS_PER_FPS for frame in frames]

    return {
        "frames": len(frames),
        "rounds_detected": rounds,
        "frames_with_position_held": held,
        "initial": {
            "alt_ft": round(first["alt_ft"], 1),
            "vc_kts": round(vc_kts, 1),
            "vt_kts": round(vt_kts, 1),
            "mach_estimate": round(first["vt_fps"] * FT_TO_M / 340.0, 3),
            "horizontal_separation_ft": round(_separation(first)[0], 0),
            "vertical_separation_ft": round(_separation(first)[1], 1),
            "slant_separation_ft": round(_separation(first)[2], 0),
            "own_heading_deg": round(first["yaw"], 2),
            # The opening attitude, because a round that starts at a different
            # pitch is a different round however well the aerodynamics agree.
            # Ours sets ic/theta-deg = 0.
            "own_pitch_deg": round(first["pitch"], 2),
            "own_alpha_deg": round(first["alpha"], 2),
        },
        "altitude_ft": {"min": round(min(altitudes), 1), "max": round(max(altitudes), 1)},
        "vc_kts": {"min": round(min(speeds), 1), "max": round(max(speeds), 1)},
        "speed_verdict": speed_verdict,
        "trim_verdict": trim_verdict(frames),
        "boundary_verdict": (
            f"{held} frames repeated the previous position exactly; "
            f"{rounds} round(s) were detected from that pattern"
        ),
    }


def _separation(frame: dict[str, Any]) -> tuple[float, float, float]:
    """Horizontal, vertical and slant range between the two aircraft, in feet.

    A degree of longitude is a degree of latitude times the cosine of the
    latitude, and the first version of this left the cosine out. At 25 degrees
    north that is a 10% error on the east component, and it turned a separation
    of 3,295 ft into a reported 3,604 — which then looked like the rules'
    3,000 / 6,000 / 9,000 being wrong rather than this being wrong.

    The state encoder always had the cosine. This is the one place that grew a
    second copy of the same arithmetic, which is the mistake the whole package
    is arranged to avoid.
    """
    earth_radius_m = 6378137.0
    latitude = math.radians(frame["lat"])
    north_m = math.radians(frame["enemy_lat"] - frame["lat"]) * earth_radius_m
    east_m = math.radians(frame["enemy_lon"] - frame["lon"]) * earth_radius_m * math.cos(latitude)
    horizontal_m = math.hypot(north_m, east_m)
    vertical_ft = frame["enemy_alt_ft"] - frame["alt_ft"]
    slant_ft = math.hypot(horizontal_m / FT_TO_M, vertical_ft)
    return horizontal_m / FT_TO_M, vertical_ft, slant_ft


def selftest(args: argparse.Namespace) -> int:
    """Send this probe synthetic packets and check the replies come back.

    Answers the one question a silent session leaves: is the probe listening and
    replying, or is nothing arriving? Those need opposite fixes — one is a bug
    here, the other is the host, its ports or the firewall — and without this
    they look identical from the terminal.
    """
    import socket as socket_module

    print("self-test: sending synthetic packets to this probe\n")
    recorder = Recorder()
    client = CompetitionClient(LevelPolicy(), observer=recorder)
    endpoint = Endpoint(
        listen_ip=args.listen_ip,
        listen_port=args.listen_port,
        host_ip=args.host_ip,
        host_port=args.host_port,
    )

    pretend_host = socket_module.socket(socket_module.AF_INET, socket_module.SOCK_DGRAM)
    pretend_host.settimeout(3.0)
    try:
        pretend_host.bind((args.host_ip, args.host_port))
    except OSError as exc:
        print(f"FAIL  could not bind {args.host_ip}:{args.host_port} — {exc}")
        print("      something else is using the host port; close it and try again")
        return 1

    listening = threading.Event()
    stop = threading.Event()
    worker = threading.Thread(
        target=serve,
        args=(client, endpoint),
        kwargs={"timeout_s": 0.2, "ready": listening, "stop": stop},
        daemon=True,
    )
    worker.start()
    if not listening.wait(timeout=5.0):
        print(f"FAIL  could not listen on {args.listen_ip}:{args.listen_port}")
        return 1
    print(f"  OK  listening on {args.listen_ip}:{args.listen_port}")

    sender = socket_module.socket(socket_module.AF_INET, socket_module.SOCK_DGRAM)
    replies = 0
    try:
        for frame in range(120):
            values = np.zeros(26, dtype=np.float64)
            values[0] = 23.060552 + (frame * 1e-5 if frame > 30 else 0.0)
            values[1] = 121.948555
            values[2] = 15_000.0
            values[12] = values[13] = 574.0
            values[20] = values[0] + 0.008
            values[21] = 121.948555
            values[22] = 15_000.0
            sender.sendto(struct.pack("<26d", *values), (args.listen_ip, args.listen_port))
            try:
                pretend_host.recvfrom(4096)
                replies += 1
            except TimeoutError:
                break
    finally:
        stop.set()
        worker.join(timeout=3.0)
        sender.close()
        pretend_host.close()

    print(f"  OK  {replies} of 120 synthetic frames were answered")
    print(f"  OK  {len(recorder.frames)} frames recorded, {client.stats.rounds_seen} round(s) seen")
    if replies < 120:
        print("\nFAIL  the probe did not answer every frame")
        return 1
    print("\nPASS  the probe listens, decides and replies.")
    print("      A silent session against the real host is therefore the host,")
    print("      its ports, or the firewall — not this program.")
    return 0


def policy_for(args: argparse.Namespace) -> LevelPolicy | NeutralPolicy:
    """What the probe flies. A function so the wiring itself can be tested.

    Selecting inline meant no test failed when the flag was ignored, which is
    the only failure mode that matters here: a --neutral run that quietly flew
    the autopilot would answer the trim question with the autopilot's own
    behaviour and look like a clean result.
    """
    return NeutralPolicy() if args.neutral else LevelPolicy()


def run(args: argparse.Namespace) -> int:
    recorder = Recorder()
    client = CompetitionClient(policy_for(args), observer=recorder)
    endpoint = Endpoint(
        listen_ip=args.listen_ip,
        listen_port=args.listen_port,
        host_ip=args.host_ip,
        host_port=args.host_port,
    )

    print(f"listening on {args.listen_ip}:{args.listen_port}")
    print(f"replying to  {args.host_ip}:{args.host_port}")
    print(f"recording for up to {args.seconds:.0f} s — start the host and press INIT, then START")
    print("Ctrl+C to stop early\n")

    listening = threading.Event()
    stop = threading.Event()
    worker = threading.Thread(
        target=serve,
        args=(client, endpoint),
        # Wakes once a second to notice `stop`, and waits through any amount of
        # silence: launching the host, pressing INIT, waiting for both players
        # and pressing START takes as long as it takes.
        kwargs={"timeout_s": 1.0, "ready": listening, "stop": stop},
        daemon=True,
    )
    worker.start()
    listening.wait(timeout=5.0)

    deadline = time.time() + args.seconds
    waiting_since = time.time()
    try:
        while worker.is_alive() and time.time() < deadline:
            time.sleep(1.0)
            if client.stats.packets_received == 0:
                print(
                    f"  waiting for the host… {time.time() - waiting_since:.0f}s "
                    f"(start it and press INIT, then START)",
                    end="\r",
                )
            else:
                print(
                    f"  {client.stats.packets_received:6d} packets, "
                    f"round {client.stats.rounds_seen}, "
                    f"frame {client.stats.frames_this_round}          ",
                    end="\r",
                )
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        stop.set()
        worker.join(timeout=3.0)

    report = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "endpoint": {
            "listen": f"{args.listen_ip}:{args.listen_port}",
            "host": f"{args.host_ip}:{args.host_port}",
        },
        "client_stats": client.stats.as_dict(),
        "analysis": analyse(recorder.frames),
    }

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    (output / f"probe-{stamp}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    with (output / f"probe-{stamp}.jsonl").open("w", encoding="utf-8") as handle:
        for frame in recorder.frames:
            handle.write(json.dumps(frame) + "\n")

    print("\n" + json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nreport: {output / f'probe-{stamp}.json'}")
    print(f"frames: {output / f'probe-{stamp}.jsonl'}")
    return 0 if recorder.frames else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    # Local self-test defaults, matching the reference client's first block.
    parser.add_argument("--listen-ip", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=8199)
    parser.add_argument("--host-ip", default="127.0.0.1")
    parser.add_argument("--host-port", type=int, default=8099)
    parser.add_argument("--seconds", type=float, default=420.0)
    parser.add_argument("--output", default="data/probe")
    parser.add_argument(
        "--neutral",
        action="store_true",
        help=(
            "send a centred stick instead of flying level, to see whether the "
            "host trims the aircraft at the start of a round. Ours does not: "
            "it loses about 19,000 ft in 49 seconds"
        ),
    )
    parser.add_argument(
        "--compare",
        metavar="RECORDING.jsonl",
        help=(
            "do not connect to anything: replay our own plant through a saved "
            "--neutral recording and report where the two traces part company"
        ),
    )
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="check this probe answers synthetic packets, without the host",
    )
    return parser.parse_args(argv)


def compare_recording(path: str) -> int:
    """`--compare`: our plant against a saved host recording, no network."""
    recording = Path(path)
    if not recording.is_file():
        # Backslashes are escape characters in the shell this is run from, so
        # a pasted Windows path arrives as one run-on word and the traceback
        # names a file nobody typed. Say what happened instead.
        print(f"no such recording: {path}", file=sys.stderr)
        if "\\" in path or (path and "/" not in path and "probe-" in path):
            print("  use forward slashes: data/probe/probe-....jsonl", file=sys.stderr)
        folder = Path("data/probe")
        if folder.is_dir():
            found = sorted(folder.glob("*.jsonl"))[-5:]
            if found:
                print("  recordings here:", file=sys.stderr)
                for item in found:
                    print(f"    {item.as_posix()}", file=sys.stderr)
        return 1
    frames = [json.loads(line) for line in recording.read_text(encoding="utf-8").splitlines() if line]
    result = replay_locally(frames)
    print()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    _args = parse_args()
    if _args.compare:
        raise SystemExit(compare_recording(_args.compare))
    raise SystemExit(selftest(_args) if _args.selftest else run(_args))
