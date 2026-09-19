"""Run lifecycle: recording, persistence and scoring (PHASE 9).

Ties together three things that each know nothing about the others — the
simulation engine, the replay recorder and the database — so that starting a
simulation also records it, finishing one also stores and scores it, and none
of those three has to grow a dependency on the other two.

Everything here is downstream of the truth state. The manager reads the world;
it never writes to it. Recording can be switched off in YAML and the simulation
behaves identically, which the determinism test asserts.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.config import Settings, get_settings
from core.event_bus import Event, EventType
from core.logging_config import get_logger
from replay.reader import load_recording
from replay.recorder import ReplayRecorder
from scoring.engine import ScoreEngine
from storage.database import Database
from storage.repository import RunRepository

if TYPE_CHECKING:  # pragma: no cover - typing only
    from core.simulation_engine import SimulationEngine

log = get_logger("run_manager")

# Rows are buffered and written in batches; a run produces thousands of
# decisions and committing each one separately is what makes a database crawl.
_FLUSH_EVERY = 250


class RunManager:
    """Records, stores and scores simulation runs."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.database: Database | None = None
        self.repository: RunRepository | None = None

        if self.settings.storage.enabled:
            path = self._resolve(self.settings.storage.database_path)
            self.database = Database(path)
            self.repository = RunRepository(
                self.database, max_decisions_per_run=self.settings.storage.max_decisions_per_run
            )

        self.recorder: ReplayRecorder | None = None
        self.run_id: str | None = None
        self.active = False

        self._engine: SimulationEngine | None = None
        self._event_cursor = 0
        self._decision_cursor = 0
        self._event_buffer: list[dict[str, Any]] = []
        self._decision_buffer: list[dict[str, Any]] = []
        self._sample_buffer: list[dict[str, Any]] = []
        self._sample_interval_ticks = 0
        self._finish_task: asyncio.Task[None] | None = None
        self._last_summary: dict[str, Any] | None = None
        # Returned by EventBus.subscribe. Held so a second run does not leave
        # the first run's handler attached to the bus.
        self._unsubscribe: Callable[[], None] | None = None

    def _resolve(self, relative: str) -> Path:
        path = Path(relative)
        return path if path.is_absolute() else self.settings.project_root / path

    # ------------------------------------------------------------- lifecycle

    def start(self, engine: SimulationEngine) -> str | None:
        """Begin recording the run the engine has loaded. Returns the run id."""
        if not self.settings.replay.enabled and self.repository is None:
            return None
        if self.active:
            # A previous run was never closed — close it before opening another,
            # so a recording can never be left open and unreadable.
            self.finish_sync("superseded by a new run")

        self._engine = engine
        self._event_cursor = 0
        self._decision_cursor = 0
        self._event_buffer.clear()
        self._decision_buffer.clear()
        self._sample_buffer.clear()
        # One telemetry sample per second: enough to chart a run, small enough
        # that a long run does not bloat the database.
        self._sample_interval_ticks = max(1, int(engine.settings.simulation.tick_rate_hz))

        if self.settings.replay.enabled:
            self.recorder = ReplayRecorder(
                engine,
                directory=self._resolve(self.settings.replay.directory),
                record_rate_hz=self.settings.replay.record_rate_hz,
                compress=self.settings.replay.compress,
                max_file_mb=self.settings.replay.max_file_mb,
            )
            self.recorder.start()
            self.run_id = self.recorder.run_id
        else:
            from replay.recorder import new_run_id

            self.recorder = None
            self.run_id = new_run_id()

        if self.repository is not None and engine.scenario is not None:
            self.repository.upsert_scenario(
                engine.scenario.name,
                description=getattr(engine.scenario, "description", "") or "",
                entity_count=len(engine.world.entities),
                duration_s=engine.scenario.duration_s,
                seed=engine.seed,
            )
            self.repository.create_run(
                self.run_id,
                scenario_name=engine.scenario.name,
                seed=engine.seed,
                config_hash=self.settings.config_hash,
                tick_rate_hz=engine.settings.simulation.tick_rate_hz,
                integrator=engine.integrator.name,
                app_version=self.settings.version,
                entities=[
                    {
                        "entity_id": e.id,
                        "team": e.team.value,
                        "agent_type": self._agent_type(engine, e.id),
                        "agent_id": self._agent_id(engine, e.id),
                    }
                    for e in engine.world.entities.values()
                ],
            )

        engine.tick_observers.append(self.on_tick)
        self._unsubscribe = engine.events.subscribe(EventType.SIMULATION_ENDED, self._on_simulation_ended)
        self.active = True

        log.info(
            "run started",
            extra={
                "event": "RUN_STARTED",
                "run_id": self.run_id,
                "scenario": engine.scenario.name if engine.scenario else None,
                "recording": self.recorder is not None,
                "storing": self.repository is not None,
            },
        )
        return self.run_id

    def on_tick(self) -> None:
        """Called by the engine after every tick. Read-only."""
        if not self.active or self._engine is None:
            return

        if self.recorder is not None:
            self.recorder.capture()

        if self.repository is None:
            return

        engine = self._engine
        events = [e.to_dict() for e in engine.events.events_after(self._event_cursor)]
        if events:
            self._event_cursor = max(int(e["sequence"]) for e in events)
            self._event_buffer.extend(events)

        decisions = [d.to_dict() for d in engine.agents.decisions_after(self._decision_cursor)]
        if decisions:
            self._decision_cursor = max(int(d["sequence"]) for d in decisions)
            self._decision_buffer.extend(decisions)

        tick = engine.world.tick
        if tick % self._sample_interval_ticks == 0:
            simulation_time = engine.world.simulation_time
            for entity in engine.world.entities.values():
                self._sample_buffer.append(
                    {
                        "entity_id": entity.id,
                        "tick": tick,
                        "simulation_time": simulation_time,
                        "x": float(entity.position[0]),
                        "y": float(entity.position[1]),
                        "altitude_m": entity.altitude,
                        "speed_mps": entity.speed,
                        "heading_deg": entity.heading_deg,
                        "status": entity.status.value,
                    }
                )

        if (
            len(self._event_buffer) >= _FLUSH_EVERY
            or len(self._decision_buffer) >= _FLUSH_EVERY
            or len(self._sample_buffer) >= _FLUSH_EVERY
        ):
            self._flush()

    def _flush(self) -> None:
        """Write buffered rows. A storage failure must never stop a simulation."""
        if self.repository is None or self.run_id is None:
            return
        try:
            if self._event_buffer:
                self.repository.add_events(self.run_id, self._event_buffer)
                self._event_buffer.clear()
            if self._decision_buffer:
                self.repository.add_decisions(self.run_id, self._decision_buffer)
                self._decision_buffer.clear()
            if self._sample_buffer:
                self.repository.add_telemetry_samples(self.run_id, self._sample_buffer)
                self._sample_buffer.clear()
        except Exception:
            log.exception("could not flush run data", extra={"event": "RUN_FLUSH_FAILED"})
            # Drop what could not be written rather than retrying forever and
            # growing without bound. The replay file remains the full record.
            self._event_buffer.clear()
            self._decision_buffer.clear()
            self._sample_buffer.clear()

    # -------------------------------------------------------------- finishing

    def _on_simulation_ended(self, event: Event) -> None:
        """The engine stopped on its own — close the run without blocking the tick."""
        reason = str(event.data.get("reason", "ended")) if event.data else "ended"
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Stepped synchronously (tests, training rollouts): finish inline.
            self.finish_sync(reason)
            return
        self._finish_task = loop.create_task(self._finish_async(reason))

    async def _finish_async(self, reason: str) -> None:
        # Closing a file and scoring a long run are both blocking; keeping them
        # off the event loop is what stops the dashboard stuttering at the end
        # of a run.
        await asyncio.to_thread(self.finish_sync, reason)

    def finish_sync(self, end_reason: str | None = None) -> dict[str, Any] | None:
        """Close the recording, persist the run and score it."""
        if not self.active or self._engine is None:
            return self._last_summary

        engine = self._engine
        self.active = False

        with contextlib.suppress(ValueError):
            engine.tick_observers.remove(self.on_tick)
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

        summary: dict[str, Any] = {"run_id": self.run_id, "end_reason": end_reason}
        if self.recorder is not None:
            summary = self.recorder.stop(end_reason)

        self._flush()

        if self.repository is not None and self.run_id is not None:
            try:
                self.repository.finish_run(
                    self.run_id,
                    end_reason=end_reason,
                    ticks=engine.world.tick,
                    simulation_time_s=engine.world.simulation_time,
                    final_state_hash=engine.world.state_hash,
                    final_statuses={e.id: e.status.value for e in engine.world.entities.values()},
                )
                if self.recorder is not None:
                    self.repository.record_replay(
                        self.run_id,
                        path=summary["path"],
                        record_rate_hz=summary["record_rate_hz"],
                        frames=summary["frames"],
                        size_bytes=summary["bytes"],
                        compressed=summary["compressed"],
                    )
            except Exception:
                log.exception("could not finalise run", extra={"event": "RUN_FINALISE_FAILED"})

        if self.settings.scoring.enabled and self.recorder is not None:
            summary["score"] = self._score(summary.get("path"))

        self._prune_recordings()
        self._last_summary = summary
        log.info("run finished", extra={"event": "RUN_FINISHED", "run_id": self.run_id, "reason": end_reason})
        return summary

    def _score(self, path: str | None) -> dict[str, Any] | None:
        """Score the finished recording and store the result."""
        if not path:
            return None
        try:
            recording = load_recording(path)
            result = ScoreEngine(self.settings.scoring).score_recording(recording)
        except Exception:
            log.exception("could not score run", extra={"event": "RUN_SCORING_FAILED"})
            return None

        if self.repository is not None and self.run_id is not None:
            with contextlib.suppress(Exception):
                for entity in result.entities:
                    self.repository.save_score(
                        self.run_id,
                        subject="entity",
                        subject_id=entity.entity_id,
                        total=entity.total,
                        breakdown={"terms": [t.to_dict() for t in entity.terms]},
                        weights_hash=result.weights_hash,
                    )
                    numeric = {
                        k: float(v)
                        for k, v in entity.metrics.to_dict().items()
                        if isinstance(v, (int, float)) and not isinstance(v, bool)
                    }
                    self.repository.save_metrics(self.run_id, entity.entity_id, numeric)
                for team, total in result.teams.items():
                    self.repository.save_score(
                        self.run_id,
                        subject="team",
                        subject_id=team,
                        total=total,
                        breakdown={},
                        weights_hash=result.weights_hash,
                    )
        return result.to_dict()

    def _prune_recordings(self) -> None:
        """Delete the oldest recordings beyond the configured cap."""
        keep = self.settings.replay.max_recordings_kept
        if keep <= 0:
            return
        directory = self._resolve(self.settings.replay.directory)
        if not directory.is_dir():
            return
        files = sorted(
            (p for p in directory.iterdir() if p.name.endswith((".jsonl", ".jsonl.gz"))),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for stale in files[keep:]:
            with contextlib.suppress(OSError):
                stale.unlink()
                log.info(
                    "pruned old recording",
                    extra={"event": "REPLAY_PRUNED", "path": str(stale), "keep": keep},
                )

    # ---------------------------------------------------------------- status

    def status(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "run_id": self.run_id,
            "recording": self.recorder is not None and self.active,
            "storing": self.repository is not None,
            "scoring_enabled": self.settings.scoring.enabled,
            "replay_directory": str(self._resolve(self.settings.replay.directory)),
            "frames": self.recorder.writer.frames if self.recorder else 0,
            "bytes": self.recorder.writer.size_bytes if self.recorder else 0,
            "truncated": self.recorder.writer.truncated if self.recorder else False,
            "database": self.database.stats() if self.database else None,
            "last_run": self._last_summary,
        }

    @staticmethod
    def _agent_type(engine: SimulationEngine, entity_id: str) -> str:
        for agent in engine.agents.agents:
            if agent.entity_id == entity_id:
                return type(agent).__name__
        return ""

    @staticmethod
    def _agent_id(engine: SimulationEngine, entity_id: str) -> str | None:
        for agent in engine.agents.agents:
            if agent.entity_id == entity_id:
                return agent.agent_id
        return None
