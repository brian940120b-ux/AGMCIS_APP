"""Replay recording (PHASE 9).

Two pieces with one job each:

* ``ReplayWriter`` — writes the JSON Lines file. Knows nothing about the
  simulation, so it is trivially testable and reusable.
* ``ReplayRecorder`` — attaches to a running engine, samples the world at the
  configured record rate, and feeds the writer.

The recorder is a **pure observer**. It reads the truth state and never writes
to it, so a run recorded and a run not recorded produce identical state hashes.
The determinism test in ``tests/test_replay.py`` asserts exactly that.
"""

from __future__ import annotations

import gzip
import json
import time
import uuid
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

from core.logging_config import get_logger
from replay import format as fmt

if TYPE_CHECKING:  # pragma: no cover - import for typing only, avoids a cycle
    from core.simulation_engine import SimulationEngine

log = get_logger("replay.recorder")


def new_run_id() -> str:
    """A sortable, human-readable run id: 20260918-142530-4f3a."""
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    return f"{stamp}-{uuid.uuid4().hex[:4]}"


class ReplayWriter:
    """Append-only JSON Lines writer, optionally gzipped."""

    def __init__(self, path: Path, *, compress: bool = True, max_bytes: int | None = None) -> None:
        self.path = path
        self.compress = compress
        self.max_bytes = max_bytes
        self.frames = 0
        self.truncated = False
        self._handle: IO[str] | None = None
        self._bytes = 0

    @property
    def is_open(self) -> bool:
        return self._handle is not None

    @property
    def size_bytes(self) -> int:
        """Bytes on disk once closed; bytes written so far while open."""
        if self._handle is None and self.path.is_file():
            return self.path.stat().st_size
        return self._bytes

    def open(self, header: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Deliberately not a context manager: the writer stays open across the
        # whole run and is closed by close(), which the recorder guarantees.
        if self.compress:
            self._handle = gzip.open(self.path, "wt", encoding="utf-8")  # noqa: SIM115
        else:
            self._handle = self.path.open("w", encoding="utf-8")
        self._write(header)

    def write_frame(self, frame: dict[str, Any]) -> bool:
        """Write one frame. Returns False once the size cap has been reached."""
        if self._handle is None or self.truncated:
            return False
        if self.max_bytes is not None and self._bytes >= self.max_bytes:
            # Stop cleanly rather than filling the disk on an unattended run.
            self.truncated = True
            log.warning(
                "replay truncated at size limit",
                extra={"event": "REPLAY_TRUNCATED", "path": str(self.path), "bytes": self._bytes},
            )
            return False
        self._write(frame)
        self.frames += 1
        return True

    def close(self, end: dict[str, Any] | None = None) -> None:
        if self._handle is None:
            return
        if end is not None:
            end = {**end, "truncated": self.truncated}
            self._write(end)
        self._handle.close()
        self._handle = None
        if self.path.is_file():
            self._bytes = self.path.stat().st_size

    def _write(self, record: dict[str, Any]) -> None:
        assert self._handle is not None
        line = json.dumps(record, separators=(",", ":"), default=str)
        self._handle.write(line + "\n")
        # Uncompressed length: an upper bound on the file, which is what the cap
        # should be conservative about.
        self._bytes += len(line) + 1


class ReplayRecorder:
    """Samples a running engine into a replay file.

    Sampling is driven by tick count rather than wall time, so a run stepped
    600 times and a run that ticked for 10 seconds record the same frames.
    """

    def __init__(
        self,
        engine: SimulationEngine,
        *,
        directory: Path,
        record_rate_hz: float = 20.0,
        compress: bool = True,
        max_file_mb: float = 200.0,
        run_id: str | None = None,
    ) -> None:
        self.engine = engine
        self.directory = Path(directory)
        self.record_rate_hz = record_rate_hz
        self.run_id = run_id or new_run_id()

        tick_rate = engine.settings.simulation.tick_rate_hz
        # One frame every N ticks. Never less than 1, so a record rate higher
        # than the tick rate simply records every tick instead of breaking.
        self.tick_interval = max(1, round(tick_rate / record_rate_hz))
        self.effective_rate_hz = tick_rate / self.tick_interval

        suffix = ".jsonl.gz" if compress else ".jsonl"
        self.path = self.directory / f"{self.run_id}{suffix}"
        self.writer = ReplayWriter(self.path, compress=compress, max_bytes=int(max_file_mb * 1024 * 1024))

        self._last_event_sequence = 0
        self._last_decision_sequence = 0
        self._last_recorded_tick = -1
        self.started_at = 0.0

    # ------------------------------------------------------------- lifecycle

    def start(self) -> None:
        """Open the file and write the header. Records the first frame too."""
        engine = self.engine
        self.started_at = time.time()
        entities = [
            {"entity_id": e.id, "team": e.team.value, "agent_type": self._agent_type(e.id)}
            for e in engine.world.entities.values()
        ]
        self.writer.open(
            fmt.header(
                run_id=self.run_id,
                scenario=engine.scenario.name if engine.scenario else "unknown",
                seed=engine.seed,
                config_hash=engine.settings.config_hash,
                integrator=engine.integrator.name,
                tick_rate_hz=engine.settings.simulation.tick_rate_hz,
                record_rate_hz=self.effective_rate_hz,
                app_version=engine.settings.version,
                started_at=self.started_at,
                entities=entities,
            )
        )
        # The initial state is a frame in its own right: without it a replay
        # starts one sample interval after the run did.
        self.capture(force=True)
        log.info(
            "recording started",
            extra={
                "event": "REPLAY_RECORDING_STARTED",
                "run_id": self.run_id,
                "path": str(self.path),
                "record_rate_hz": self.effective_rate_hz,
            },
        )

    def capture(self, *, force: bool = False) -> bool:
        """Record a frame if this tick is a sample point. Returns True if written."""
        if not self.writer.is_open:
            return False

        tick = self.engine.world.tick
        if not force and (tick == self._last_recorded_tick or tick % self.tick_interval != 0):
            return False

        events = [e.to_dict() for e in self.engine.events.events_after(self._last_event_sequence)]
        decisions = [d.to_dict() for d in self.engine.agents.decisions_after(self._last_decision_sequence)]
        if events:
            self._last_event_sequence = max(int(e["sequence"]) for e in events)
        if decisions:
            self._last_decision_sequence = max(int(d["sequence"]) for d in decisions)

        written = self.writer.write_frame(
            fmt.world_frame(self.engine.world, events=events, decisions=decisions)
        )
        if written:
            self._last_recorded_tick = tick
        return written

    def stop(self, end_reason: str | None = None) -> dict[str, Any]:
        """Write the final frame and close the file. Returns a summary."""
        if not self.writer.is_open:
            return self.summary(end_reason)

        # Capture the terminal state, whatever tick it landed on.
        self.capture(force=True)
        world = self.engine.world
        self.writer.close(
            fmt.end(
                frames=self.writer.frames,
                ticks=world.tick,
                simulation_time=world.simulation_time,
                end_reason=end_reason,
                final_state_hash=world.state_hash,
            )
        )
        summary = self.summary(end_reason)
        log.info("recording stopped", extra={"event": "REPLAY_RECORDING_STOPPED", **summary})
        return summary

    # ---------------------------------------------------------------- status

    def summary(self, end_reason: str | None = None) -> dict[str, Any]:
        world = self.engine.world
        return {
            "run_id": self.run_id,
            "path": str(self.path),
            "frames": self.writer.frames,
            "bytes": self.writer.size_bytes,
            "compressed": self.writer.compress,
            "record_rate_hz": self.effective_rate_hz,
            "truncated": self.writer.truncated,
            "ticks": world.tick,
            "simulation_time_s": world.simulation_time,
            "final_state_hash": world.state_hash,
            "end_reason": end_reason,
        }

    def _agent_type(self, entity_id: str) -> str:
        for agent in self.engine.agents.agents:
            if agent.entity_id == entity_id:
                return type(agent).__name__
        return ""
