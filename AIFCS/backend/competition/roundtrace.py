"""A round, frame by frame, in the platform's replay format.

Everything we know about a policy so far is an average of twenty rounds. That
was enough to find that v6 reaches the cone and does not stay — `cone 0.38 s`
against `<5deg 10.1 s`, a ratio of 3.8% where the solid angle alone gives 4% —
but it cannot say *when*. Was that 0.38 seconds one pass or six? What was the
aircraft doing on the way in, and what made it leave? A mean over twenty rounds
has no answer, and no amount of further averaging will produce one.

So this records the round itself: one line per frame, with the geometry the
scoring reads and the state of the kill counter beside it. The file is the same
JSON Lines envelope the platform already records simulations into — header,
frames, end — written with the same `ReplayWriter`, so the platform's reader,
its size caps and its gzip come for free.

The frame body is competition-specific rather than `world_frame`, which needs a
`WorldState` a competition round does not have. What it carries is what the
competition is decided on: range, the two angles, whether this frame is inside
the firing envelope, and how many seconds have accumulated towards the three a
kill needs.

Recording is off unless asked for. Eighteen thousand frames a round is a few
megabytes before gzip, and most rounds are not worth keeping.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from competition.scoring import AttackEnvelope
from replay.format import SCOPE_NOTICE
from replay.recorder import ReplayWriter

#: The recording's own version, separate from the platform's FORMAT_VERSION,
#: because the frame body here is this module's and may change on its own.
TRACE_VERSION = 1


class RoundTrace:
    """Writes one round to one file."""

    def __init__(
        self,
        path: Path,
        *,
        label: str,
        opponent: str,
        seed: int,
        envelope: AttackEnvelope,
        plant: dict[str, Any] | None = None,
        compress: bool = True,
        max_bytes: int | None = 64 * 1024 * 1024,
    ) -> None:
        self.writer = ReplayWriter(path, compress=compress, max_bytes=max_bytes)
        self.label = label
        self.opponent = opponent
        self.seed = seed
        self.envelope = envelope
        self.plant = plant or {}
        self.frame = 0

    def open(self) -> None:
        self.writer.open(
            {
                "kind": "header",
                "trace_version": TRACE_VERSION,
                "label": self.label,
                "opponent": self.opponent,
                "seed": self.seed,
                "tick_rate_hz": 60.0,
                "started_at": time.time(),
                # The envelope is in the header because a trace read next year
                # should not have to guess which rules it was scored under.
                "envelope": {
                    "half_angle_deg": self.envelope.half_angle_deg,
                    "min_range_ft": self.envelope.min_range_ft,
                    "max_range_ft": self.envelope.max_range_ft,
                    "kill_seconds": self.envelope.kill_seconds,
                },
                "plant": self.plant,
                "notice": SCOPE_NOTICE,
            }
        )

    def record(
        self,
        geometry: Any,
        *,
        attack_seconds: float,
        g_load: float,
        floor_active: bool,
    ) -> None:
        self.frame += 1
        inside = self.envelope.contains(geometry)
        self.writer.write_frame(
            {
                "kind": "frame",
                "frame": self.frame,
                "t": round(self.frame / 60.0, 4),
                "distance_m": round(float(geometry.distance_m), 2),
                "track_angle_deg": round(float(geometry.track_angle_deg), 4),
                "aspect_angle_deg": round(float(geometry.aspect_angle_deg), 3),
                "own_alt_m": round(float(geometry.own_alt_m), 1),
                "enemy_alt_m": round(float(geometry.enemy_alt_m), 1),
                "g_load": round(float(g_load), 3),
                # The two that answer the question this file exists for.
                "in_envelope": inside,
                "attack_seconds": round(float(attack_seconds), 4),
                "floor_active": bool(floor_active),
            }
        )

    def close(self, *, outcome: dict[str, Any], reason: str) -> None:
        self.writer.close(
            {
                "kind": "end",
                "frames": self.frame,
                "simulation_time": round(self.frame / 60.0, 3),
                "end_reason": reason,
                "outcome": outcome,
            }
        )


def read_trace(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """A trace back as (header, frames, end).

    Small enough to hold in memory — one round is eighteen thousand frames —
    and the caller usually wants all of them at once to draw a line.
    """
    import gzip
    import json

    opener = gzip.open if path.suffix == ".gz" else open
    header: dict[str, Any] = {}
    frames: list[dict[str, Any]] = []
    end: dict[str, Any] = {}
    with opener(path, "rt", encoding="utf-8") as handle:  # type: ignore[operator]
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            kind = record.get("kind")
            if kind == "header":
                header = record
            elif kind == "frame":
                frames.append(record)
            elif kind == "end":
                end = record
    return header, frames, end


def passes(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The runs of consecutive frames inside the envelope.

    The number this exists to produce: how many separate times the policy got
    into the cone, and how long it stayed on each. `cone 0.38 s` is the sum;
    one pass of 0.38 s and six passes of 0.06 s are different problems with
    different answers, and the mean cannot tell them apart.
    """
    out: list[dict[str, Any]] = []
    start: int | None = None
    for index, frame in enumerate(frames):
        if frame.get("in_envelope"):
            if start is None:
                start = index
        elif start is not None:
            out.append(_pass(frames, start, index))
            start = None
    if start is not None:
        out.append(_pass(frames, start, len(frames)))
    return out


def _pass(frames: list[dict[str, Any]], start: int, stop: int) -> dict[str, Any]:
    window = frames[start:stop]
    return {
        "start_s": window[0]["t"],
        "end_s": window[-1]["t"],
        "seconds": round(len(window) / 60.0, 3),
        "closest_deg": round(min(f["track_angle_deg"] for f in window), 3),
        "mean_distance_m": round(sum(f["distance_m"] for f in window) / len(window), 1),
    }


def summarise(path: Path) -> str:
    """One round, read back as prose.

    The chart is better and it is coming. But the question — one long pass or
    six short ones — is answerable from a terminal today, and a number you can
    see today beats a picture you can see next week.
    """
    header, frames, end = read_trace(path)
    envelope = header.get("envelope", {})
    runs = passes(frames)
    seconds = sum(run["seconds"] for run in runs)
    needed = float(envelope.get("kill_seconds", 3.0))

    lines = [
        f"{path.name}",
        f"  {header.get('label', '?')} vs {header.get('opponent', '?')}, seed {header.get('seed')}",
        f"  {len(frames)} frames ({len(frames) / 60.0:.0f} s), ended {end.get('end_reason', '?')}",
        "",
        f"  inside the {envelope.get('half_angle_deg', 1.0)}-degree cone:"
        f" {len(runs)} pass(es), {seconds:.2f} s in total, {needed:.2f} s needed",
    ]
    if not runs:
        closest = min((f["track_angle_deg"] for f in frames), default=180.0)
        lines.append(f"  never got in. Closest the nose came: {closest:.1f} degrees")
    for index, run in enumerate(runs, start=1):
        lines.append(
            f"    {index:>2}. {run['start_s']:>6.1f}-{run['end_s']:>6.1f} s"
            f"  {run['seconds']:>5.2f} s"
            f"  closest {run['closest_deg']:>5.2f} deg"
            f"  at {run['mean_distance_m']:>6.0f} m"
        )
    if runs:
        longest = max(run["seconds"] for run in runs)
        lines.append("")
        lines.append(
            f"  longest single pass {longest:.2f} s. "
            + (
                "Long enough on its own."
                if longest >= needed
                else f"It has to hold {needed - longest:.2f} s longer, or get in more often."
            )
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Read a recorded round back.")
    parser.add_argument("traces", nargs="+", type=Path, help="files written by evaluate --trace")
    parser.add_argument(
        "--html",
        type=Path,
        default=None,
        metavar="DIR",
        help=(
            "also write each round as a standalone page in DIR. No network calls, "
            "so it opens from a file:// path with no server running"
        ),
    )
    args = parser.parse_args(argv)
    for path in args.traces:
        print()
        print(summarise(path))
        if args.html is not None:
            from competition.trace_html import render

            args.html.mkdir(parents=True, exist_ok=True)
            out = args.html / (path.name.split(".")[0] + ".html")
            out.write_text(render(path), encoding="utf-8")
            print(f"  page: {out}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
