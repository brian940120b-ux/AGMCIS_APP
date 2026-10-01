"""Measure the aircraft we actually fly: turn rate, radius and G against entry speed.

    python backend/competition/turntable.py [--altitude-ft 15000] [--seconds 3]

The USAF handbook (AFTTP 3-3 Vol 5, SRC-022) gives the real F-16 a "corner
plateau" at 330-440 KCAS, a quickest turn of 20.1 deg/s and a tightest radius
of 1,942 ft on its 15,000 ft EM diagram. Those numbers are the real aircraft's.
The competition flies JSBSim's F-16, which is a public 1979 wind-tunnel model,
and the brief (PART 43) forbids feeding unverified external aero data into
anything. So the table that may be used is the one measured here, from the
same plant the environment and the host run, and the handbook's figures are
only the thing to compare it against.

Method, per entry speed: trim-free level start at the altitude, full nose-up
elevator with the stick shaped exactly as the competition shapes it (the cube,
the slew limit and the 9-G limiter on), wings level, full throttle, for
`seconds`. Record the peak heading rate, the peak load factor, the radius at
that instant (V^2 / (g * sqrt(n^2 - 1))) and the speed bled. Instantaneous
performance, not sustained: the question a dogfight asks first is how hard the
aircraft can turn right now.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from competition.action import JoystickState, shape_command  # noqa: E402
from competition.environment import SIM_HZ, Aircraft, resolve_jsbsim_root  # noqa: E402

G_FPS2 = 32.174
FT_PER_M = 1.0 / 0.3048
SPEEDS_KCAS: tuple[float, ...] = (200.0, 250.0, 300.0, 330.0, 360.0, 400.0, 440.0, 480.0, 520.0, 560.0)


@dataclass
class TurnPoint:
    entry_kcas: float
    peak_rate_deg_s: float
    peak_g: float
    radius_ft_at_peak: float
    kcas_at_peak: float
    seconds_to_peak: float
    kcas_after: float
    altitude_lost_ft: float
    max_g: float = 0.0


def measure(
    entry_kcas: float, *, altitude_ft: float, seconds: float, g_limit: float | None = 9.0
) -> TurnPoint:
    aircraft = Aircraft(
        root=resolve_jsbsim_root(None),
        lat_deg=25.0,
        lon_deg=121.0,
        altitude_ft=altitude_ft,
        heading_deg=0.0,
        speed_kcas=entry_kcas,
    )
    joystick = JoystickState()
    fdm = aircraft.fdm
    best = TurnPoint(entry_kcas, 0.0, 0.0, math.inf, entry_kcas, 0.0, entry_kcas, 0.0)
    start_alt = float(fdm["position/h-sl-ft"])
    frames = int(seconds * SIM_HZ)
    window = int(0.5 * SIM_HZ)

    def velocity() -> np.ndarray:
        v = np.array(
            [fdm["velocities/v-north-fps"], fdm["velocities/v-east-fps"], fdm["velocities/v-down-fps"]],
            dtype=np.float64,
        )
        return v / max(float(np.linalg.norm(v)), 1e-9)

    previous = velocity()
    rates: list[float] = []
    loads: list[float] = []
    for frame in range(frames):
        telemetry_mach = float(fdm["velocities/vt-fps"]) * 0.3048 / 340.0
        g_load = float(fdm["accelerations/n-pilot-z-norm"])
        # Full pull, wings level, full throttle, through the competition's stick.
        command = shape_command(
            np.array([0.0, -1.0, 0.0, 1.0]),
            joystick,
            telemetry_mach,
            g_load=None if g_limit is None else g_load,
            g_limit=g_limit,
        )
        aircraft.apply(command)
        aircraft.step()
        # Turn rate as the rotation of the flight path, not the body: the body
        # pitch rate spikes while the angle of attack is still building and
        # read 25 deg/s at under five G, which no turn-rate formula allows.
        current = velocity()
        rates.append(math.degrees(math.acos(float(np.clip(np.dot(previous, current), -1.0, 1.0)))) * SIM_HZ)
        loads.append(abs(float(fdm["accelerations/n-pilot-z-norm"])))
        previous = current
        if len(rates) >= window:
            rate = float(np.mean(rates[-window:]))
            n = float(np.mean(loads[-window:]))
            if rate > best.peak_rate_deg_s:
                vt_fps = float(fdm["velocities/vt-fps"])
                radius = vt_fps * vt_fps / (G_FPS2 * math.sqrt(n * n - 1.0)) if n > 1.0 else math.inf
                best = TurnPoint(
                    entry_kcas=entry_kcas,
                    peak_rate_deg_s=rate,
                    peak_g=n,
                    radius_ft_at_peak=radius,
                    kcas_at_peak=float(fdm["velocities/vc-kts"]),
                    seconds_to_peak=(frame + 1) / SIM_HZ,
                    kcas_after=0.0,
                    altitude_lost_ft=0.0,
                )
    best.max_g = max(loads) if loads else 0.0
    best.kcas_after = float(fdm["velocities/vc-kts"])
    best.altitude_lost_ft = start_alt - float(fdm["position/h-sl-ft"])
    return best


def table(points: list[TurnPoint]) -> str:
    lines = [
        f"{'entry':>6} {'peak':>7} {'G@pk':>5} {'maxG':>5} {'radius':>8} {'KCAS@pk':>8} "
        f"{'t@pk':>5} {'KCAS end':>9} {'alt lost':>9}",
        f"{'KCAS':>6} {'deg/s':>7} {'':>5} {'':>5} {'ft':>8} {'':>8} {'s':>5} {'':>9} {'ft':>9}",
    ]
    for p in points:
        radius = f"{p.radius_ft_at_peak:8.0f}" if math.isfinite(p.radius_ft_at_peak) else f"{'inf':>8}"
        lines.append(
            f"{p.entry_kcas:6.0f} {p.peak_rate_deg_s:7.1f} {p.peak_g:5.1f} {p.max_g:5.1f} {radius} "
            f"{p.kcas_at_peak:8.0f} {p.seconds_to_peak:5.2f} {p.kcas_after:9.0f} {p.altitude_lost_ft:9.0f}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--altitude-ft", type=float, default=15_000.0)
    parser.add_argument("--seconds", type=float, default=3.0)
    parser.add_argument("--no-g-limit", action="store_true", help="full elevator with no 9-G limiter")
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)
    g_limit = None if args.no_g_limit else 9.0
    points = [
        measure(k, altitude_ft=args.altitude_ft, seconds=args.seconds, g_limit=g_limit) for k in SPEEDS_KCAS
    ]
    print(f"JSBSim F-16, {args.altitude_ft:.0f} ft, full pull for {args.seconds:.0f} s, G limit {g_limit}\n")
    print(table(points))
    best = max(points, key=lambda p: p.peak_rate_deg_s)
    print(f"\nquickest turn {best.peak_rate_deg_s:.1f} deg/s at {best.entry_kcas:.0f} KCAS entry;", end=" ")
    print("handbook (real F-16A, 15,000 ft EM diagram): 20.1 deg/s")
    if args.json:
        args.json.write_text(json.dumps([asdict(p) for p in points], indent=2), encoding="utf-8")
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
