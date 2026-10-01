"""Read the organiser's host recording and check our scoring against its columns.

    python backend/competition/hostcsv.py "path/to/飛行競賽Host端Record_....csv"

The host writes one row per frame with both players' state, their commands,
and its own running score columns. That makes it the one source that can say
whether our scoring engine computes what the judge computes, frame by frame,
rather than whether it computes what we read in the announcement.

Two questions, answered separately:

1. **Which normalisation is the host using?** Both candidates in
   `scoring.Normalisation` are rebuilt from the positions and attitudes in the
   CSV and compared with the host's own `AttackAdvantage` and
   `PositionAdvantage` columns. The one with the smaller largest difference is
   the one the host runs. On 2026-10-01 that was HOST, at 0.0000.
2. **What happened in the round?** Start geometry, separation over time, time
   in the attack envelope, the host's final figures and who it says won.

Read-only. The CSV stays where it is; nothing in the repository is written.
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

# Runnable as a file as well as importable. The path has to be set before the
# package imports below, not inside the __main__ guard underneath them.
_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from competition.scoring import Normalisation, distance_factor  # noqa: E402

METRES_PER_DEGREE = 111_320.0
FT_TO_M = 0.3048
FRAME_HZ = 60.0

#: The per-player columns this needs. Anything else the host writes is ignored,
#: so a column added in a later host version does not break the read.
NEEDED = (
    "lat_deg",
    "lon_deg",
    "alt_ft",
    "pitch_deg",
    "yaw_deg",
    "ias_kts",
    "g_acc",
    "Score_AttackAdvantage",
    "Score_PositionAdvantage",
    "Score_FinalAdvantage",
    "Score_RemainingHP",
)


@dataclass
class HostRound:
    """The CSV as arrays, one per column, both players."""

    time: np.ndarray
    columns: dict[str, np.ndarray]
    winner: list[str]

    @property
    def frames(self) -> int:
        return len(self.time)

    def col(self, player: str, name: str) -> np.ndarray:
        return self.columns[f"{player}_{name}"]


def read_host_csv(path: Path) -> HostRound:
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"{path} has no rows")
    missing = [f"{p}_{c}" for p in ("player1", "player2") for c in NEEDED if f"{p}_{c}" not in rows[0]]
    if missing:
        raise ValueError(f"{path} lacks columns: {', '.join(missing)}")
    columns = {
        key: np.array([float(row[key]) for row in rows], dtype=np.float64)
        for key in rows[0]
        if key not in ("time", "system_Final_whoiswinner")
    }
    return HostRound(
        time=np.array([float(row["time"]) for row in rows]),
        columns=columns,
        winner=[row.get("system_Final_whoiswinner", "") for row in rows],
    )


# ---------------------------------------------------------------- geometry


def _nose(hr: HostRound, player: str) -> np.ndarray:
    yaw = np.radians(hr.col(player, "yaw_deg"))
    pitch = np.radians(hr.col(player, "pitch_deg"))
    return np.stack([np.cos(pitch) * np.cos(yaw), np.cos(pitch) * np.sin(yaw), -np.sin(pitch)], axis=1)


def _angle_deg(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.degrees(np.arccos(np.clip((a * b).sum(axis=1), -1.0, 1.0)))


@dataclass
class Angles:
    distance_m: np.ndarray
    track_deg: np.ndarray
    aspect_deg: np.ndarray

    @property
    def factor(self) -> np.ndarray:
        return np.array([distance_factor(float(d)) for d in self.distance_m])


def angles(hr: HostRound, me: str, foe: str) -> Angles:
    """TA and AA as the announcement defines them, in NED, from the CSV."""
    lat1, lon1 = hr.col(me, "lat_deg"), hr.col(me, "lon_deg")
    lat2, lon2 = hr.col(foe, "lat_deg"), hr.col(foe, "lon_deg")
    north = (lat2 - lat1) * METRES_PER_DEGREE
    east = (lon2 - lon1) * METRES_PER_DEGREE * np.cos(np.radians(lat1))
    down = -(hr.col(foe, "alt_ft") - hr.col(me, "alt_ft")) * FT_TO_M
    los = np.stack([north, east, down], axis=1)
    distance = np.linalg.norm(los, axis=1)
    unit = los / np.maximum(distance, 1e-9)[:, None]
    return Angles(
        distance_m=distance,
        # Our nose against the line to them.
        track_deg=_angle_deg(_nose(hr, me), unit),
        # Their nose against the same line: 0 when we sit on their tail.
        aspect_deg=_angle_deg(_nose(hr, foe), unit),
    )


def normalised(angle_deg: np.ndarray, normalisation: Normalisation) -> np.ndarray:
    inside = angle_deg < 90.0
    if normalisation is Normalisation.HOST:
        return np.where(inside, (180.0 - angle_deg) / 180.0, 0.0)
    return np.where(inside, (90.0 - angle_deg) / 90.0, 0.0)


# ----------------------------------------------------------------- checks


@dataclass
class Fit:
    normalisation: Normalisation
    attack_max_diff: float
    position_max_diff: float
    final_max_step_diff: float

    @property
    def worst(self) -> float:
        return max(self.attack_max_diff, self.position_max_diff, self.final_max_step_diff)


def check_scoring(hr: HostRound, me: str = "player1", foe: str = "player2") -> list[Fit]:
    """Rebuild the host's three score columns under each normalisation.

    `Final` is checked as per-frame steps (Att + Pos of that frame), because
    the host also accrues during the pre-START hold and the CSV starts after
    it, so the absolute value carries an offset the formula cannot see.
    """
    geo = angles(hr, me, foe)
    factor = geo.factor
    attack = hr.col(me, "Score_AttackAdvantage")
    position = hr.col(me, "Score_PositionAdvantage")
    final = hr.col(me, "Score_FinalAdvantage")
    fits = []
    for normalisation in Normalisation:
        att = normalised(geo.track_deg, normalisation) * factor
        pos = normalised(geo.aspect_deg, normalisation) * factor
        steps = np.diff(final)
        fits.append(
            Fit(
                normalisation=normalisation,
                attack_max_diff=float(np.abs(att - attack).max()),
                position_max_diff=float(np.abs(pos - position).max()),
                final_max_step_diff=float(np.abs(steps - (att + pos)[1:]).max()) if len(steps) else 0.0,
            )
        )
    return fits


def summary(hr: HostRound, me: str = "player1", foe: str = "player2") -> dict[str, Any]:
    geo = angles(hr, me, foe)
    feet = geo.distance_m / FT_TO_M
    in_range = (feet >= 500.0) & (feet <= 3000.0)
    cone = in_range & (geo.track_deg <= 1.0)
    hold = int((np.diff(hr.col(me, "lat_deg")) == 0).sum())
    return {
        "frames": hr.frames,
        "seconds": float(hr.time[-1] - hr.time[0]) if hr.frames > 1 else 0.0,
        "start": {
            "separation_m": float(geo.distance_m[0]),
            "own_heading_deg": float(hr.col(me, "yaw_deg")[0]),
            "foe_heading_deg": float(hr.col(foe, "yaw_deg")[0]),
            "own_alt_ft": float(hr.col(me, "alt_ft")[0]),
            "foe_alt_ft": float(hr.col(foe, "alt_ft")[0]),
            "own_ias_kts": float(hr.col(me, "ias_kts")[0]),
            "track_deg": float(geo.track_deg[0]),
        },
        "separation_m": {
            "min": float(geo.distance_m.min()),
            "max": float(geo.distance_m.max()),
            "end": float(geo.distance_m[-1]),
        },
        "seconds_in_range_500_3000ft": float(in_range.sum() / FRAME_HZ),
        "seconds_in_cone": float(cone.sum() / FRAME_HZ),
        "best_track_deg": float(geo.track_deg.min()),
        "max_g": float(np.abs(hr.col(me, "g_acc")).max()),
        "frozen_frames_in_csv": hold,
        "host": {
            "own_final": float(hr.col(me, "Score_FinalAdvantage")[-1]),
            "foe_final": float(hr.col(foe, "Score_FinalAdvantage")[-1]),
            "own_hp": float(hr.col(me, "Score_RemainingHP")[-1]),
            "foe_hp": float(hr.col(foe, "Score_RemainingHP")[-1]),
            "winner": hr.winner[-1],
        },
    }


# ------------------------------------------------------------------- main


def describe(path: Path, me: str = "player1") -> int:
    foe = "player2" if me == "player1" else "player1"
    hr = read_host_csv(path)
    fits = check_scoring(hr, me, foe)
    facts = summary(hr, me, foe)
    print(f"{path}: {facts['frames']} frames, {facts['seconds']:.1f} s, we are {me}\n")
    print("scoring columns rebuilt from positions and attitudes, largest difference per column:")
    for fit in fits:
        print(
            f"  {fit.normalisation.value:<13} attack {fit.attack_max_diff:.4f}   "
            f"position {fit.position_max_diff:.4f}   final step {fit.final_max_step_diff:.4f}"
        )
    best = min(fits, key=lambda f: f.worst)
    print(
        f"  -> the host computes the {best.normalisation.value} normalisation"
        + (" (exact)" if best.worst < 1e-3 else "")
    )
    s = facts["start"]
    headings = f"{s['own_heading_deg']:.0f} / {s['foe_heading_deg']:.0f}"
    altitudes = f"{s['own_alt_ft']:.0f} / {s['foe_alt_ft']:.0f} ft"
    print(f"\nstart: {s['separation_m']:.0f} m apart, headings {headings}, {altitudes},", end=" ")
    print(f"{s['own_ias_kts']:.0f} kt IAS, nose {s['track_deg']:.0f} deg off")
    sep = facts["separation_m"]
    print(f"separation: min {sep['min']:.0f} m, max {sep['max']:.0f} m, end {sep['end']:.0f} m")
    print(f"in range 500-3000 ft {facts['seconds_in_range_500_3000ft']:.1f} s,", end=" ")
    print(f"inside the 1-degree cone {facts['seconds_in_cone']:.2f} s,", end=" ")
    print(f"best track {facts['best_track_deg']:.2f} deg, max |G| {facts['max_g']:.1f}")
    h = facts["host"]
    print(f"host: final {h['own_final']:.0f} vs {h['foe_final']:.0f},", end=" ")
    print(f"HP {h['own_hp']:.0f} vs {h['foe_hp']:.0f}, winner column {h['winner']}")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("csv", help="the host's 飛行競賽Host端Record_*.csv")
    parser.add_argument(
        "--player", choices=["player1", "player2"], default="player1", help="which one we were"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    path = Path(args.csv)
    if not path.is_file():
        print(f"no such file: {path}", file=sys.stderr)
        return 2
    try:
        return describe(path, args.player)
    except ValueError as error:
        print(f"cannot read {path}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
