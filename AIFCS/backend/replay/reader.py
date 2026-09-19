"""Reading recordings back (PHASE 9).

Both the replay player and the scoring engine consume recordings, so loading
and validating one lives here and neither has to know about gzip or JSON Lines.

A recording is loaded fully into memory. At the default 20 Hz a ten-minute run
is about twelve thousand frames, which is comfortable; a recording large enough
to be a problem is one the size cap should already have truncated.
"""

from __future__ import annotations

import gzip
import json
from bisect import bisect_left
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.logging_config import get_logger

log = get_logger("replay.reader")

# Events worth being able to jump between. A tick event is not one of them.
NOTABLE_EVENT_TYPES = frozenset(
    {
        "SIMULATION_STARTED",
        "SIMULATION_PAUSED",
        "SIMULATION_RESUMED",
        "SIMULATION_ENDED",
        "ENTITY_OUT_OF_BOUNDS",
        "COLLISION",
        "ACTION_REJECTED",
        "COMMUNICATION_EVENT",
        "SCORE_CHANGED",
    }
)


class ReplayError(RuntimeError):
    """Raised when a recording is missing, unreadable or malformed."""


def _open_text(path: Path) -> Any:
    """Open a recording, detecting gzip by magic bytes rather than by name.

    A recording renamed by hand should still open, and a ``.gz`` file that is
    not actually gzipped should fail with a clear error rather than a decode
    error a hundred lines later.
    """
    with path.open("rb") as probe:
        magic = probe.read(2)
    if magic == b"\x1f\x8b":
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open("r", encoding="utf-8")


@dataclass
class Recording:
    """A loaded replay: its header, its frames and how it ended."""

    path: Path
    header: dict[str, Any]
    frames: list[dict[str, Any]]
    end: dict[str, Any] | None = None
    # Parallel to `frames`, for seeking by tick without rescanning.
    _ticks: list[int] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self._ticks = [int(f.get("tick", 0)) for f in self.frames]

    # ------------------------------------------------------------- identity

    @property
    def run_id(self) -> str:
        return str(self.header.get("run_id", self.path.stem))

    @property
    def scenario(self) -> str:
        return str(self.header.get("scenario", "unknown"))

    @property
    def seed(self) -> int:
        return int(self.header.get("seed", 0))

    @property
    def tick_rate_hz(self) -> float:
        return float(self.header.get("tick_rate_hz", 60.0))

    @property
    def record_rate_hz(self) -> float:
        return float(self.header.get("record_rate_hz", 20.0))

    @property
    def frame_count(self) -> int:
        return len(self.frames)

    @property
    def duration_s(self) -> float:
        return float(self.frames[-1].get("simulation_time", 0.0)) if self.frames else 0.0

    @property
    def entity_ids(self) -> list[str]:
        if self.header.get("entities"):
            return [str(e["entity_id"]) for e in self.header["entities"]]
        return [str(e["id"]) for e in self.frames[0].get("entities", [])] if self.frames else []

    @property
    def complete(self) -> bool:
        """False when the run crashed or was killed before writing its trailer."""
        return self.end is not None

    @property
    def truncated(self) -> bool:
        return bool(self.end and self.end.get("truncated"))

    # -------------------------------------------------------------- seeking

    def frame(self, index: int) -> dict[str, Any] | None:
        if 0 <= index < len(self.frames):
            return self.frames[index]
        return None

    def index_for_tick(self, tick: int) -> int:
        """Index of the first frame at or after ``tick``, clamped to the range."""
        if not self.frames:
            return 0
        position = bisect_left(self._ticks, tick)
        return min(position, len(self.frames) - 1)

    def index_for_time(self, simulation_time: float) -> int:
        return self.index_for_tick(round(simulation_time * self.tick_rate_hz))

    # --------------------------------------------------------------- events

    def event_index(self) -> list[dict[str, Any]]:
        """Frame indices carrying a notable event, for jump-to-event.

        Built once and cached on the instance: a long recording is scanned
        repeatedly by the transport controls otherwise.
        """
        cached = getattr(self, "_event_index", None)
        if cached is not None:
            return list(cached)

        index: list[dict[str, Any]] = []
        for position, frame in enumerate(self.frames):
            for event in frame.get("events", []):
                if event.get("type") in NOTABLE_EVENT_TYPES:
                    index.append(
                        {
                            "frame": position,
                            "tick": int(frame.get("tick", 0)),
                            "simulation_time": float(frame.get("simulation_time", 0.0)),
                            "type": str(event.get("type", "")),
                            "entity_id": event.get("entity_id"),
                            "message": str(event.get("message", "")),
                        }
                    )
        self._event_index = index  # type: ignore[attr-defined]
        return list(index)

    def summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "path": str(self.path),
            "scenario": self.scenario,
            "seed": self.seed,
            "config_hash": self.header.get("config_hash"),
            "tick_rate_hz": self.tick_rate_hz,
            "record_rate_hz": self.record_rate_hz,
            "frames": self.frame_count,
            "duration_s": round(self.duration_s, 3),
            "entities": self.entity_ids,
            "complete": self.complete,
            "truncated": self.truncated,
            "end_reason": (self.end or {}).get("end_reason"),
            "final_state_hash": (self.end or {}).get("final_state_hash"),
            "size_bytes": self.path.stat().st_size if self.path.is_file() else 0,
        }


def load_recording(path: Path | str) -> Recording:
    """Load and validate a recording."""
    path = Path(path)
    if not path.is_file():
        raise ReplayError(f"recording not found: {path}")

    header: dict[str, Any] | None = None
    frames: list[dict[str, Any]] = []
    end: dict[str, Any] | None = None
    malformed = 0

    with _open_text(path) as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except (ValueError, json.JSONDecodeError):
                # A run killed mid-write leaves a partial final line. One is
                # expected and harmless; many mean the file is not a recording.
                malformed += 1
                if malformed > 1 or header is None:
                    raise ReplayError(f"{path}: malformed JSON on line {line_number}") from None
                continue

            kind = record.get("kind")
            if kind == "header":
                header = record
            elif kind == "frame":
                frames.append(record)
            elif kind == "end":
                end = record

    if header is None:
        raise ReplayError(f"{path}: no header record — not an AIFCS recording")
    if malformed:
        log.warning(
            "recording ends in a partial line; loading what is there",
            extra={"event": "REPLAY_PARTIAL", "path": str(path), "frames": len(frames)},
        )

    return Recording(path=path, header=header, frames=frames, end=end)


def list_recordings(directory: Path | str) -> list[dict[str, Any]]:
    """Every recording in a directory, newest first, without loading frames.

    Only the header is read, so listing a directory of large recordings stays
    fast. A file that fails to parse is reported rather than hidden, because a
    recording that silently vanishes from the list is worse than a broken row.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return []

    out: list[dict[str, Any]] = []
    for path in directory.iterdir():
        if not path.is_file() or not path.name.endswith((".jsonl", ".jsonl.gz")):
            continue
        stat = path.stat()
        entry: dict[str, Any] = {
            "run_id": path.name.split(".")[0],
            "path": str(path),
            "filename": path.name,
            "size_bytes": stat.st_size,
            "modified_at": stat.st_mtime,
            "readable": True,
        }
        try:
            with _open_text(path) as handle:
                first = handle.readline()
            record = json.loads(first)
            if record.get("kind") != "header":
                raise ValueError("first line is not a header")
            entry.update(
                {
                    "run_id": record.get("run_id", entry["run_id"]),
                    "scenario": record.get("scenario"),
                    "seed": record.get("seed"),
                    "config_hash": record.get("config_hash"),
                    "record_rate_hz": record.get("record_rate_hz"),
                    "started_at": record.get("started_at"),
                }
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            entry["readable"] = False
            entry["error"] = str(exc)

        out.append(entry)

    out.sort(key=lambda e: e.get("modified_at", 0.0), reverse=True)
    return out
