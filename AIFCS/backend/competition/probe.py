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
            "separation_ft": round(_separation_ft(first), 0),
        },
        "altitude_ft": {"min": round(min(altitudes), 1), "max": round(max(altitudes), 1)},
        "vc_kts": {"min": round(min(speeds), 1), "max": round(max(speeds), 1)},
        "speed_verdict": speed_verdict,
        "boundary_verdict": (
            f"{held} frames repeated the previous position exactly; "
            f"{rounds} round(s) were detected from that pattern"
        ),
    }


def _separation_ft(frame: dict[str, Any]) -> float:
    metres_per_degree = 111_320.0
    north = (frame["enemy_lat"] - frame["lat"]) * metres_per_degree
    east = (frame["enemy_lon"] - frame["lon"]) * metres_per_degree
    up = (frame["enemy_alt_ft"] - frame["alt_ft"]) * FT_TO_M
    return float(math.sqrt(north * north + east * east + up * up)) / FT_TO_M


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


def run(args: argparse.Namespace) -> int:
    recorder = Recorder()
    client = CompetitionClient(LevelPolicy(), observer=recorder)
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
        "--selftest",
        action="store_true",
        help="check this probe answers synthetic packets, without the host",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    _args = parse_args()
    raise SystemExit(selftest(_args) if _args.selftest else run(_args))
