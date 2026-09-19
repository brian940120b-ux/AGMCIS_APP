"""Scenario editing endpoints (PHASE 10).

Reading scenarios already existed. This router lets them be written: create,
update, clone, delete, import, export, and validate a draft without saving it.

Three refusals are deliberate and are what make an editor safe to expose:

* a name that is not a plain identifier never becomes a filename;
* an invalid document is never written, so every file on disk loads;
* the scenario a run is currently using cannot be overwritten or deleted out
  from under it.

Scenarios remain fictional and abstract. Nothing here accepts, validates
against, or produces real-world platform data.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

from core.config import Settings, get_settings
from core.logging_config import get_logger
from core.runtime import get_engine
from core.simulation_engine import SimulationEngine
from simulation.scenario import ScenarioError, list_scenarios
from simulation.scenario_store import DEFAULT_TEMPLATE, ScenarioStore, dump_yaml

log = get_logger("api.scenarios")
router = APIRouter(tags=["scenarios"])


class ImportRequest(BaseModel):
    yaml_text: str = Field(..., description="A complete scenario file as text.")
    name: str | None = Field(default=None, description="Store under this name instead of the declared one.")
    overwrite: bool = Field(default=False)


class CloneRequest(BaseModel):
    new_name: str = Field(..., description="Name for the copy.")


def get_store(settings: Settings = Depends(get_settings)) -> ScenarioStore:
    directory = Path(settings.scenarios.directory)
    if not directory.is_absolute():
        directory = settings.project_root / directory
    # The default scenario is protected: without it the backend cannot start a
    # run, and the operator would have to read the config to find out why.
    return ScenarioStore(directory, protected=(settings.scenarios.default_scenario,))


def _bad_request(exc: ScenarioError) -> HTTPException:
    """A rejected scenario is the caller's problem, not a server fault."""
    return HTTPException(status_code=422, detail=str(exc))


def _guard_running(engine: SimulationEngine, name: str, action: str) -> None:
    """Refuse to change the scenario a run is currently flying."""
    if engine.is_running and engine.scenario is not None and engine.scenario.name == name:
        raise HTTPException(
            status_code=409,
            detail=f"cannot {action} {name!r} while a run of it is in progress — stop the simulation first",
        )


# ------------------------------------------------------------------- reading


@router.get("/scenarios")
def scenarios(
    settings: Settings = Depends(get_settings),
    engine: SimulationEngine = Depends(get_engine),
    store: ScenarioStore = Depends(get_store),
) -> dict[str, Any]:
    """Available scenarios, with a reason for any that will not load."""
    return {
        "directory": str(settings.scenarios.directory),
        "default": settings.scenarios.default_scenario,
        "available": list_scenarios(store.directory),
        "scenarios": store.summaries(),
        "loaded": engine.scenario.to_dict() if engine.scenario else None,
    }


@router.get("/scenarios/template")
def scenario_template() -> dict[str, Any]:
    """A minimal valid scenario to start a new one from.

    Served rather than hard-coded in the UI so the editor's starting point
    cannot drift away from what the validator accepts.
    """
    return {"document": DEFAULT_TEMPLATE, "yaml": dump_yaml(DEFAULT_TEMPLATE)}


@router.get("/scenarios/{name}")
def scenario_detail(name: str, store: ScenarioStore = Depends(get_store)) -> dict[str, Any]:
    try:
        scenario = store.get(name)
    except ScenarioError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        **scenario.to_dict(),
        "protected": store.is_protected(name),
        "document": scenario.to_document(),
    }


@router.get("/scenarios/{name}/export")
def scenario_export(name: str, store: ScenarioStore = Depends(get_store)) -> dict[str, Any]:
    """The file's own text, comments included."""
    try:
        return {"name": name, "yaml": store.export_yaml(name)}
    except ScenarioError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ------------------------------------------------------------------- writing


@router.post("/scenarios/validate")
def scenario_validate(document: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Check a draft without saving it.

    The editor calls this as you type, so a mistake is reported next to the
    field rather than at the end of a long form.
    """
    try:
        scenario = ScenarioStore.validate(document)
    except ScenarioError as exc:
        return {"valid": False, "error": str(exc)}
    return {"valid": True, "scenario": scenario.to_dict()}


@router.post("/scenarios")
def scenario_create(
    document: dict[str, Any] = Body(...),
    store: ScenarioStore = Depends(get_store),
) -> dict[str, Any]:
    try:
        scenario = store.create(document)
    except ScenarioError as exc:
        raise _bad_request(exc) from exc
    return {**scenario.to_dict(), "protected": store.is_protected(scenario.name)}


@router.put("/scenarios/{name}")
def scenario_update(
    name: str,
    document: dict[str, Any] = Body(...),
    store: ScenarioStore = Depends(get_store),
    engine: SimulationEngine = Depends(get_engine),
) -> dict[str, Any]:
    _guard_running(engine, name, "overwrite")
    try:
        scenario = store.update(name, document)
    except ScenarioError as exc:
        status = 404 if "no such scenario" in str(exc) else 422
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return {**scenario.to_dict(), "protected": store.is_protected(scenario.name)}


@router.post("/scenarios/{name}/clone")
def scenario_clone(
    name: str,
    request: CloneRequest,
    store: ScenarioStore = Depends(get_store),
) -> dict[str, Any]:
    try:
        scenario = store.clone(name, request.new_name)
    except ScenarioError as exc:
        status = 404 if "no such scenario" in str(exc) else 422
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return {**scenario.to_dict(), "protected": False}


@router.post("/scenarios/import")
def scenario_import(
    request: ImportRequest,
    store: ScenarioStore = Depends(get_store),
) -> dict[str, Any]:
    try:
        scenario = store.import_yaml(request.yaml_text, name=request.name, overwrite=request.overwrite)
    except ScenarioError as exc:
        raise _bad_request(exc) from exc
    return {**scenario.to_dict(), "protected": store.is_protected(scenario.name)}


@router.delete("/scenarios/{name}")
def scenario_delete(
    name: str,
    store: ScenarioStore = Depends(get_store),
    engine: SimulationEngine = Depends(get_engine),
) -> dict[str, Any]:
    _guard_running(engine, name, "delete")
    try:
        removed = store.delete(name)
    except ScenarioError as exc:
        raise _bad_request(exc) from exc
    if not removed:
        raise HTTPException(status_code=404, detail=f"no such scenario: {name}")

    # The engine may still be pointing at the file that has just gone. Left
    # alone it keeps reporting it as loaded, the dashboard pre-selects it, and
    # the next START fails with "scenario file not found".
    unloaded = False
    if engine.scenario is not None and engine.scenario.name == name:
        engine.unload()
        unloaded = True

    return {"name": name, "deleted": True, "unloaded_from_engine": unloaded}
