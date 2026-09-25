"""Replay, run history and scoring endpoints (PHASE 9).

Every control here does what it says. The transport genuinely moves the
playback cursor, ``rescore`` genuinely recomputes a score from the recording on
disk, and deleting a run genuinely removes its rows and its file. Nothing is a
placeholder.

Replay is read-only with respect to the simulation: loading and playing a
recording cannot start, alter or interfere with a live run.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from core.config import Settings, get_settings
from core.logging_config import get_logger
from core.run_manager import RunManager
from core.runtime import get_replay_player, get_run_manager
from replay.player import ReplayPlayer
from replay.reader import ReplayError, list_recordings, load_recording
from scoring.engine import ScoreEngine

log = get_logger("api.replay")
router = APIRouter(tags=["replay"])


# --------------------------------------------------------------- request models


class LoadRequest(BaseModel):
    run_id: str | None = Field(default=None, description="Recording to load, by run id.")
    path: str | None = Field(default=None, description="Explicit path; must be inside the replay directory.")


class StepRequest(BaseModel):
    frames: int = Field(default=1, ge=-100_000, le=100_000, description="Frames to move; negative rewinds.")


class SeekRequest(BaseModel):
    frame: int | None = Field(default=None, ge=0)
    tick: int | None = Field(default=None, ge=0)
    simulation_time: float | None = Field(default=None, ge=0.0)


class SpeedRequest(BaseModel):
    speed: float = Field(..., gt=0, description="Playback multiplier; must be an allowed speed.")


class JumpRequest(BaseModel):
    direction: int = Field(default=1, description="1 for the next event, -1 for the previous one.")


# ---------------------------------------------------------------------- helpers


def _replay_directory(settings: Settings) -> Path:
    directory = Path(settings.replay.directory)
    return directory if directory.is_absolute() else settings.project_root / directory


# A run id is minted by the platform as `YYYYMMDD-HHMMSS-xxxx`. Anything else
# is not a run id, and a path separator in one would turn every endpoint that
# builds a filename from it into a way to reach the rest of the disk. The `path`
# branch below was already guarded; the id branch was not, which made
# `DELETE /api/runs/../../something` an arbitrary file delete.
_VALID_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _require_run_id(run_id: str) -> str:
    if not _VALID_RUN_ID.match(run_id):
        raise HTTPException(status_code=400, detail=f"not a valid run id: {run_id!r}")
    return run_id


def _resolve_recording(settings: Settings, run_id: str | None, path: str | None) -> Path:
    """Turn a run id or a path into a file inside the replay directory.

    A caller-supplied path is resolved and checked to be inside the replay
    directory. Without that check, a path parameter would let any file on the
    machine be read back through the API.
    """
    directory = _replay_directory(settings).resolve()

    if path:
        candidate = Path(path)
        candidate = candidate if candidate.is_absolute() else directory / candidate
        candidate = candidate.resolve()
        if not candidate.is_relative_to(directory):
            raise HTTPException(status_code=400, detail="path must be inside the replay directory")
        if not candidate.is_file():
            raise HTTPException(status_code=404, detail=f"recording not found: {path}")
        return candidate

    if not run_id:
        raise HTTPException(status_code=400, detail="provide either run_id or path")

    _require_run_id(run_id)
    for suffix in (".jsonl.gz", ".jsonl"):
        candidate = directory / f"{run_id}{suffix}"
        if candidate.is_file():
            return candidate
    raise HTTPException(status_code=404, detail=f"no recording for run {run_id}")


def _require_repository(runs: RunManager) -> Any:
    if runs.repository is None:
        raise HTTPException(
            status_code=503,
            detail="run storage is disabled — set storage.enabled in configs/analysis.yaml",
        )
    return runs.repository


def _player_error(exc: ReplayError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


# ---------------------------------------------------------------- recordings


@router.get("/replay/recordings")
def recordings(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Every recording on disk, newest first. Only headers are read."""
    directory = _replay_directory(settings)
    found = list_recordings(directory)
    return {
        "directory": str(directory),
        "count": len(found),
        "recordings": found,
        "enabled": settings.replay.enabled,
    }


@router.post("/replay/load")
def replay_load(
    request: LoadRequest,
    settings: Settings = Depends(get_settings),
    player: ReplayPlayer = Depends(get_replay_player),
) -> dict[str, Any]:
    path = _resolve_recording(settings, request.run_id, request.path)
    try:
        player.load(path)
    except ReplayError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return player.status()


@router.post("/replay/unload")
async def replay_unload(player: ReplayPlayer = Depends(get_replay_player)) -> dict[str, Any]:
    await player.close()
    return player.status()


# ----------------------------------------------------------------- transport


@router.post("/replay/play")
async def replay_play(player: ReplayPlayer = Depends(get_replay_player)) -> dict[str, Any]:
    try:
        await player.play()
    except ReplayError as exc:
        raise _player_error(exc) from exc
    return player.status()


@router.post("/replay/pause")
def replay_pause(player: ReplayPlayer = Depends(get_replay_player)) -> dict[str, Any]:
    if not player.loaded:
        raise HTTPException(status_code=409, detail="no recording loaded")
    player.pause()
    return player.status()


@router.post("/replay/step")
def replay_step(
    request: StepRequest | None = None,
    player: ReplayPlayer = Depends(get_replay_player),
) -> dict[str, Any]:
    body = request or StepRequest()
    try:
        player.step(body.frames)
    except ReplayError as exc:
        raise _player_error(exc) from exc
    return player.status()


@router.post("/replay/seek")
def replay_seek(
    request: SeekRequest,
    player: ReplayPlayer = Depends(get_replay_player),
) -> dict[str, Any]:
    if request.frame is None and request.tick is None and request.simulation_time is None:
        raise HTTPException(status_code=400, detail="provide frame, tick or simulation_time")
    try:
        if request.frame is not None:
            player.seek_frame(request.frame)
        elif request.tick is not None:
            player.seek_tick(request.tick)
        else:
            player.seek_time(float(request.simulation_time or 0.0))
    except ReplayError as exc:
        raise _player_error(exc) from exc
    return player.status()


@router.post("/replay/speed")
def replay_speed(
    request: SpeedRequest,
    player: ReplayPlayer = Depends(get_replay_player),
) -> dict[str, Any]:
    try:
        player.set_speed(request.speed)
    except ReplayError as exc:
        raise _player_error(exc) from exc
    return player.status()


@router.post("/replay/jump")
def replay_jump(
    request: JumpRequest | None = None,
    player: ReplayPlayer = Depends(get_replay_player),
) -> dict[str, Any]:
    body = request or JumpRequest()
    try:
        target = player.jump_to_event(body.direction)
    except ReplayError as exc:
        raise _player_error(exc) from exc
    return {**player.status(), "jumped_to": target}


# -------------------------------------------------------------------- output


@router.get("/replay/status")
def replay_status(player: ReplayPlayer = Depends(get_replay_player)) -> dict[str, Any]:
    return player.status()


@router.get("/replay/frame")
def replay_frame(player: ReplayPlayer = Depends(get_replay_player)) -> dict[str, Any]:
    """The frame the cursor is on, plus where the cursor is."""
    if not player.loaded:
        raise HTTPException(status_code=409, detail="no recording loaded")
    return {"status": player.status(), "frame": player.current_frame()}


@router.get("/replay/events")
def replay_events(player: ReplayPlayer = Depends(get_replay_player)) -> dict[str, Any]:
    """Notable events in the loaded recording, for the timeline markers."""
    if player.recording is None:
        raise HTTPException(status_code=409, detail="no recording loaded")
    index = player.recording.event_index()
    return {"count": len(index), "events": index}


# ----------------------------------------------------------------- run history


@router.get("/runs")
def runs_list(
    limit: int = Query(default=50, ge=1, le=500),
    scenario: str | None = Query(default=None),
    runs: RunManager = Depends(get_run_manager),
) -> dict[str, Any]:
    repository = _require_repository(runs)
    found = repository.list_runs(limit=limit, scenario_name=scenario)
    return {"count": len(found), "runs": found, "current": runs.status()}


@router.get("/runs/current")
def runs_current(runs: RunManager = Depends(get_run_manager)) -> dict[str, Any]:
    """Recording and storage state for the run happening right now."""
    return runs.status()


@router.get("/runs/{run_id}")
def run_detail(run_id: str, runs: RunManager = Depends(get_run_manager)) -> dict[str, Any]:
    repository = _require_repository(runs)
    run = repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"no such run: {run_id}")
    return run


@router.get("/runs/{run_id}/decisions")
def run_decisions(
    run_id: str,
    limit: int = Query(default=200, ge=1, le=5000),
    agent_id: str | None = Query(default=None),
    runs: RunManager = Depends(get_run_manager),
) -> dict[str, Any]:
    repository = _require_repository(runs)
    found = repository.get_decisions(run_id, limit=limit, agent_id=agent_id)
    return {"count": len(found), "decisions": found}


@router.get("/runs/{run_id}/events")
def run_events(
    run_id: str,
    limit: int = Query(default=200, ge=1, le=5000),
    event_type: str | None = Query(default=None),
    runs: RunManager = Depends(get_run_manager),
) -> dict[str, Any]:
    repository = _require_repository(runs)
    found = repository.get_events(run_id, limit=limit, event_type=event_type)
    return {"count": len(found), "events": found}


@router.get("/runs/{run_id}/telemetry")
def run_telemetry(
    run_id: str,
    entity_id: str | None = Query(default=None),
    limit: int = Query(default=2000, ge=1, le=20000),
    runs: RunManager = Depends(get_run_manager),
) -> dict[str, Any]:
    """One-per-second samples, for charting a run without opening its replay."""
    repository = _require_repository(runs)
    found = repository.get_telemetry_samples(run_id, entity_id=entity_id, limit=limit)
    return {"count": len(found), "samples": found}


@router.delete("/runs/{run_id}")
def run_delete(
    run_id: str,
    delete_recording: bool = Query(default=True),
    runs: RunManager = Depends(get_run_manager),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Delete a run's rows, and by default its recording file too."""
    repository = _require_repository(runs)
    _require_run_id(run_id)
    if runs.active and runs.run_id == run_id:
        raise HTTPException(status_code=409, detail="that run is still recording — stop it first")

    removed_rows = repository.delete_run(run_id)
    removed_file = False
    if delete_recording:
        directory = _replay_directory(settings)
        for suffix in (".jsonl.gz", ".jsonl"):
            candidate = directory / f"{run_id}{suffix}"
            if candidate.is_file():
                candidate.unlink()
                removed_file = True

    if not removed_rows and not removed_file:
        raise HTTPException(status_code=404, detail=f"no such run: {run_id}")
    return {"run_id": run_id, "rows_deleted": removed_rows, "recording_deleted": removed_file}


# --------------------------------------------------------------------- scoring


@router.get("/scoring/weights")
def scoring_weights(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """The standard runs are judged against. Two scores only compare if the hash matches."""
    engine = ScoreEngine(settings.scoring)
    return {
        "enabled": settings.scoring.enabled,
        "weights": settings.scoring.weights.model_dump(),
        "thresholds": settings.scoring.thresholds.model_dump(),
        "redistribute_inapplicable": settings.scoring.redistribute_inapplicable,
        "max_points": settings.scoring.weights.total(),
        "weights_hash": engine.weights_hash,
        "notice": (
            "Flight- and mission-quality terms only. AIFCS models no weapon, "
            "engagement or targeting capability, and scores none."
        ),
    }


@router.post("/runs/{run_id}/score")
async def run_score(
    run_id: str,
    persist: bool = Query(default=True, description="Store the result against the run."),
    runs: RunManager = Depends(get_run_manager),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Recompute a run's score from its recording, under the current weights.

    Rescoring is how a changed weight or threshold is applied to runs that
    already happened — the recording is the source of truth, so the score can
    always be rebuilt.
    """
    path = _resolve_recording(settings, run_id, None)

    def compute() -> dict[str, Any]:
        recording = load_recording(path)
        result = ScoreEngine(settings.scoring).score_recording(recording)
        if persist and runs.repository is not None:
            for entity in result.entities:
                runs.repository.save_score(
                    run_id,
                    subject="entity",
                    subject_id=entity.entity_id,
                    total=entity.total,
                    breakdown={"terms": [t.to_dict() for t in entity.terms]},
                    weights_hash=result.weights_hash,
                )
            for team, total in result.teams.items():
                runs.repository.save_score(
                    run_id,
                    subject="team",
                    subject_id=team,
                    total=total,
                    breakdown={},
                    weights_hash=result.weights_hash,
                )
        return result.to_dict()

    try:
        # Scoring reads and walks the whole recording; off the event loop.
        return await asyncio.to_thread(compute)
    except ReplayError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
