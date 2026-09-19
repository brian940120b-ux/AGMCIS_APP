"""Replay playback (PHASE 9).

Playback is a cursor over a loaded recording. The cursor is server-side for the
same reason the simulation is: there is then exactly one answer to "where are
we", whether it is asked by the 3D view, the 2D plot or a second browser tab.

The player never touches the simulation engine. A replay is a recording being
read back; it cannot start, alter or interfere with a live run. Playing one
while a simulation is running is allowed, and the two simply do not interact.

Transport: play, pause, step (forward or back), seek by frame, tick or time,
speed change, and jump to the next or previous notable event.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any

from core.logging_config import get_logger
from replay.reader import Recording, ReplayError, load_recording

log = get_logger("replay.player")


class ReplayPlayer:
    """Plays a recording back at a controllable rate."""

    def __init__(self, allowed_speeds: tuple[float, ...] = (0.25, 0.5, 1.0, 2.0, 5.0, 10.0)) -> None:
        self.recording: Recording | None = None
        self.index = 0
        self.speed = 1.0
        self.playing = False
        self.allowed_speeds = tuple(allowed_speeds)
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------- lifecycle

    def load(self, path: Path | str) -> Recording:
        """Load a recording and park the cursor at the first frame."""
        recording = load_recording(path)
        if recording.frame_count == 0:
            raise ReplayError(f"{path}: recording contains no frames")
        self.recording = recording
        self.index = 0
        self.playing = False
        log.info(
            "replay loaded",
            extra={
                "event": "REPLAY_LOADED",
                "run_id": recording.run_id,
                "frames": recording.frame_count,
                "duration_s": round(recording.duration_s, 2),
            },
        )
        return recording

    async def close(self) -> None:
        """Stop playback and unload. Safe to call when nothing is loaded."""
        await self.stop()
        self.recording = None
        self.index = 0

    def _require(self) -> Recording:
        if self.recording is None:
            raise ReplayError("no recording loaded")
        return self.recording

    # ------------------------------------------------------------- transport

    async def play(self) -> None:
        """Start advancing the cursor. Replaying from the end rewinds first."""
        recording = self._require()
        async with self._lock:
            if self._task is not None and not self._task.done():
                self.playing = True
                return
            if self.index >= recording.frame_count - 1:
                self.index = 0
            self.playing = True
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Pause and cancel the playback task."""
        async with self._lock:
            self.playing = False
            task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    def pause(self) -> None:
        """Hold position. The task stays alive and resumes on the next play()."""
        self.playing = False

    def step(self, frames: int = 1) -> int:
        """Move the cursor by a number of frames, forwards or backwards."""
        recording = self._require()
        self.playing = False
        self.index = max(0, min(recording.frame_count - 1, self.index + frames))
        return self.index

    def seek_frame(self, index: int) -> int:
        recording = self._require()
        self.index = max(0, min(recording.frame_count - 1, index))
        return self.index

    def seek_tick(self, tick: int) -> int:
        recording = self._require()
        self.index = recording.index_for_tick(tick)
        return self.index

    def seek_time(self, simulation_time: float) -> int:
        recording = self._require()
        self.index = recording.index_for_time(simulation_time)
        return self.index

    def set_speed(self, speed: float) -> float:
        if speed not in self.allowed_speeds:
            raise ReplayError(f"speed {speed} not allowed; choose one of {list(self.allowed_speeds)}")
        self.speed = speed
        return self.speed

    def jump_to_event(self, direction: int = 1) -> dict[str, Any] | None:
        """Move to the next (1) or previous (-1) notable event. None if there is none."""
        recording = self._require()
        events = recording.event_index()
        if not events:
            return None

        self.playing = False
        if direction >= 0:
            candidates = [e for e in events if e["frame"] > self.index]
            target = candidates[0] if candidates else None
        else:
            candidates = [e for e in events if e["frame"] < self.index]
            target = candidates[-1] if candidates else None

        if target is None:
            return None
        self.index = int(target["frame"])
        return target

    # ----------------------------------------------------------------- loop

    async def _run(self) -> None:
        """Advance one frame per record interval, scaled by the speed."""
        recording = self._require()
        loop = asyncio.get_running_loop()
        interval = 1.0 / recording.record_rate_hz
        next_at = loop.time()

        try:
            while True:
                if not self.playing:
                    await asyncio.sleep(0.02)
                    next_at = loop.time()
                    continue

                if self.index >= recording.frame_count - 1:
                    self.playing = False
                    log.info(
                        "replay reached the end",
                        extra={"event": "REPLAY_ENDED", "run_id": recording.run_id},
                    )
                    await asyncio.sleep(0.05)
                    continue

                self.index += 1

                next_at += interval / self.speed
                delay = next_at - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                else:
                    # Behind schedule: yield and stop chasing a backlog.
                    await asyncio.sleep(0)
                    next_at = loop.time()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("replay loop failed", extra={"event": "REPLAY_LOOP_ERROR"})
            self.playing = False
            raise

    # ---------------------------------------------------------------- output

    @property
    def loaded(self) -> bool:
        return self.recording is not None

    def current_frame(self) -> dict[str, Any] | None:
        if self.recording is None:
            return None
        return self.recording.frame(self.index)

    def status(self) -> dict[str, Any]:
        """Everything the dashboard needs to draw the transport bar."""
        if self.recording is None:
            return {"loaded": False, "playing": False}

        recording = self.recording
        frame = recording.frame(self.index) or {}
        last = max(recording.frame_count - 1, 1)
        return {
            "loaded": True,
            "playing": self.playing,
            "speed": self.speed,
            "allowed_speeds": list(self.allowed_speeds),
            "frame_index": self.index,
            "frame_count": recording.frame_count,
            "progress": round(self.index / last, 6),
            "tick": int(frame.get("tick", 0)),
            "simulation_time": round(float(frame.get("simulation_time", 0.0)), 3),
            "duration_s": round(recording.duration_s, 3),
            "state_hash": frame.get("state_hash"),
            "at_end": self.index >= recording.frame_count - 1,
            "recording": recording.summary(),
            "event_count": len(recording.event_index()),
        }
