"""Simulation control and world-state endpoints (PHASE 1).

Every endpoint here acts on the real engine: ``pause`` genuinely stops the
simulation clock, ``step`` genuinely advances the world, ``reset`` genuinely
rebuilds it from the scenario. Nothing is a placeholder.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from core.event_bus import EventType
from core.run_manager import RunManager
from core.runtime import get_engine, get_run_manager
from core.simulation_engine import SimulationEngine, SimulationError
from simulation.scenario import ScenarioError

router = APIRouter(tags=["simulation"])


class StartRequest(BaseModel):
    scenario: str | None = Field(default=None, description="Scenario name; default from config.")
    seed: int | None = Field(default=None, description="Override the scenario's random seed.")


class StepRequest(BaseModel):
    ticks: int = Field(default=1, ge=1, le=100_000, description="Fixed timesteps to advance.")


class SpeedRequest(BaseModel):
    speed: float = Field(..., gt=0, description="Wall-clock multiplier; must be an allowed speed.")


def _fail(exc: SimulationError | ScenarioError) -> HTTPException:
    """Engine refusals are client errors, not server faults."""
    return HTTPException(status_code=409, detail=str(exc))


@router.get("/simulation/status")
def simulation_status(engine: SimulationEngine = Depends(get_engine)) -> dict[str, Any]:
    return engine.status()


@router.post("/simulation/start")
async def simulation_start(
    request: StartRequest | None = None,
    engine: SimulationEngine = Depends(get_engine),
    runs: RunManager = Depends(get_run_manager),
) -> dict[str, Any]:
    body = request or StartRequest()
    try:
        await engine.start(scenario=body.scenario, seed=body.seed)
    except (SimulationError, ScenarioError) as exc:
        raise _fail(exc) from exc

    # Recording begins after the engine has accepted the run, so a refused
    # start never leaves an empty recording behind.
    runs.start(engine)
    return {**engine.status(), "run": runs.status()}


@router.post("/simulation/pause")
def simulation_pause(engine: SimulationEngine = Depends(get_engine)) -> dict[str, Any]:
    try:
        engine.pause()
    except SimulationError as exc:
        raise _fail(exc) from exc
    return engine.status()


@router.post("/simulation/resume")
def simulation_resume(engine: SimulationEngine = Depends(get_engine)) -> dict[str, Any]:
    try:
        engine.resume()
    except SimulationError as exc:
        raise _fail(exc) from exc
    return engine.status()


@router.post("/simulation/stop")
async def simulation_stop(
    engine: SimulationEngine = Depends(get_engine),
    runs: RunManager = Depends(get_run_manager),
) -> dict[str, Any]:
    await engine.stop()
    # Closing the recording writes a file and scores it, so it goes to a thread
    # rather than blocking the event loop the dashboard is being served from.
    summary = await asyncio.to_thread(runs.finish_sync, "stopped by operator")
    return {**engine.status(), "run": runs.status(), "summary": summary}


@router.post("/simulation/reset")
async def simulation_reset(
    engine: SimulationEngine = Depends(get_engine),
    runs: RunManager = Depends(get_run_manager),
) -> dict[str, Any]:
    # Close the run before the world is rebuilt: after reset the final state is
    # the scenario's initial state, which is not how the run actually ended.
    summary = await asyncio.to_thread(runs.finish_sync, "reset by operator")
    await engine.reset()
    return {**engine.status(), "run": runs.status(), "summary": summary}


@router.post("/simulation/step")
def simulation_step(
    request: StepRequest | None = None,
    engine: SimulationEngine = Depends(get_engine),
) -> dict[str, Any]:
    body = request or StepRequest()
    try:
        engine.step(body.ticks)
    except (SimulationError, ScenarioError) as exc:
        raise _fail(exc) from exc
    return engine.status()


@router.post("/simulation/speed")
def simulation_speed(
    request: SpeedRequest,
    engine: SimulationEngine = Depends(get_engine),
) -> dict[str, Any]:
    try:
        engine.set_speed(request.speed)
    except SimulationError as exc:
        raise _fail(exc) from exc
    return engine.status()


@router.get("/world/state")
def world_state(engine: SimulationEngine = Depends(get_engine)) -> dict[str, Any]:
    """Full truth state. Agents never see this — they get an Observation (PHASE 5)."""
    payload = engine.world.to_dict()
    payload["state_hash"] = engine.world.state_hash
    payload["clock"] = engine.clock.snapshot()
    return payload


@router.get("/entities")
def entities(engine: SimulationEngine = Depends(get_engine)) -> dict[str, Any]:
    return {
        "count": len(engine.world.entities),
        "entities": [e.to_dict() for e in engine.world.entities.values()],
    }


@router.get("/events")
def events(
    limit: int = Query(default=50, ge=1, le=500),
    event_type: str | None = Query(default=None),
    engine: SimulationEngine = Depends(get_engine),
) -> dict[str, Any]:
    """Recent system events, oldest first. SIMULATION_TICK is not retained."""
    selected: EventType | None = None
    if event_type is not None:
        try:
            selected = EventType(event_type)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"unknown event type: {event_type}") from exc

    recent = engine.events.recent(limit=limit, event_type=selected)
    return {"count": len(recent), "events": [e.to_dict() for e in recent]}


@router.get("/sensors")
def sensors(engine: SimulationEngine = Depends(get_engine)) -> dict[str, Any]:
    """Sensor model configuration and how many contacts each unit is holding."""
    return engine.sensors.status()


@router.get("/communications")
def communications(engine: SimulationEngine = Depends(get_engine)) -> dict[str, Any]:
    """Datalink configuration, traffic statistics and blackout state."""
    return {
        **engine.comms.status(engine.clock.simulation_time),
        "datalink": engine.datalink.status(),
    }


@router.get("/controller")
def controller(engine: SimulationEngine = Depends(get_engine)) -> dict[str, Any]:
    """Flight controller statistics: what it applied, rejected and corrected."""
    return engine.controller.status()
