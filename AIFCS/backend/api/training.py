"""Training endpoints (PHASE 11-13, control added in PHASE 18).

Until PHASE 18 this router was read-only and said so: a START button that could
not report progress, could not be cancelled and could not survive a reload would
have been a control that did not do what it appeared to. Those three things now
exist, so the button does.

What is still true, and still stated rather than hidden: a job runs **in this
server process**, one at a time, and does not survive a restart. The command
line remains the right place for a long run.

The RL stack is an optional dependency. Every endpoint works without it and
says plainly that it is missing, rather than failing to import.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from core.config import Settings, get_settings
from core.logging_config import get_logger
from core.run_manager import RunManager
from core.runtime import get_run_manager, get_training_runner
from training.jobs import (
    TrainingBusyError,
    TrainingJobRunner,
    TrainingRefusedError,
)
from training.pipeline import (
    ALGORITHMS,
    TrainingPipeline,
    TrainingUnavailableError,
    resolve_device,
    rl_available,
    rl_status,
)
from training.registry import (
    ModelIncompatibleError,
    ModelNotFoundError,
    ModelRegistry,
)
from training.reward import TERM_NAMES

log = get_logger("api.training")
router = APIRouter(tags=["training"])

INSTALL_HINT = ".venv/bin/pip install -r requirements-ml.txt"
CLI_HINT = "cd backend && ../.venv/bin/python train.py --algorithm ppo --timesteps 200000"


def _pipeline(settings: Settings) -> TrainingPipeline:
    return TrainingPipeline(settings=settings)


@router.get("/training/status")
def training_status(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """What the training stack can do right now."""
    status = rl_status()
    available = status.available
    training = settings.training
    return {
        "available": available,
        # Only when installing would help. On a machine where the stack is
        # installed but will not load, "install it" is the one useless answer.
        "install_hint": status.install_hint,
        "unavailable_reason": status.reason,
        "how_to_run": CLI_HINT,
        "browser_control": available,
        "browser_control_note": (
            "A job can be started here. It runs in this server process, one at a time, "
            "and does not survive a restart — a long run belongs on the command line."
        ),
        "max_timesteps_per_job": training.max_timesteps_per_job,
        "device": resolve_device(training.device) if available else "unavailable",
        "configured_device": training.device,
        "algorithms": list(ALGORITHMS),
        "scenario": training.scenario,
        "entity_id": training.entity_id,
        "max_episode_seconds": training.max_episode_seconds,
        "output_directory": training.output_directory,
        "hyperparameters": {
            "ppo": training.ppo.model_dump(),
            "sac": training.sac.model_dump(),
        },
    }


@router.get("/training/environment")
def training_environment(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """The environment a policy is trained against."""
    if not rl_available():
        raise HTTPException(status_code=503, detail=f"RL stack not installed. {INSTALL_HINT}")

    from training.environment import AIFCSCombatEnv

    env = AIFCSCombatEnv(
        scenario_name=settings.training.scenario,
        entity_id=settings.training.entity_id,
        settings=settings,
        max_episode_seconds=settings.training.max_episode_seconds,
    )
    try:
        return env.spec_summary()
    finally:
        env.close()


@router.get("/training/reward")
def training_reward(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """The reward a policy is optimising, term by term."""
    return {
        "terms": list(TERM_NAMES),
        "weights": settings.training.reward_weights.model_dump(),
        "notice": (
            "Flight and navigation terms only. AIFCS models no weapon, engagement "
            "or targeting capability, and rewards none."
        ),
    }


@router.get("/training/models")
def training_models(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Saved policies, newest first, each with the card it was saved with."""
    if not rl_available():
        return {"available": False, "count": 0, "models": [], "install_hint": INSTALL_HINT}
    models = _pipeline(settings).list_models()
    return {"available": True, "count": len(models), "models": models}


@router.get("/training/runs")
def training_runs(
    limit: int = Query(default=50, ge=1, le=500),
    runs: RunManager = Depends(get_run_manager),
) -> dict[str, Any]:
    """Recorded training runs, from the PHASE 9 database."""
    if runs.repository is None:
        raise HTTPException(status_code=503, detail="run storage is disabled")
    rows = runs.repository.db.query("SELECT * FROM training_runs ORDER BY started_at DESC LIMIT ?", (limit,))
    return {"count": len(rows), "runs": [dict(r) for r in rows]}


# ------------------------------------------------------------------- PHASE 18


class StartTrainingRequest(BaseModel):
    """What to train. Everything has a default from configs/training.yaml."""

    algorithm: str = Field(default="ppo", description="ppo or sac")
    timesteps: int | None = Field(default=None, ge=1, description="Defaults to the configured budget")
    seed: int | None = Field(default=None, description="Defaults to the simulation seed")
    evaluate_episodes: int = Field(default=0, ge=0, le=50, description="Episodes to evaluate after")


@router.get("/training/jobs")
def training_jobs(runner: TrainingJobRunner = Depends(get_training_runner)) -> dict[str, Any]:
    """The job running now, if any, and the ones this server has run."""
    return runner.status()


@router.get("/training/jobs/{job_id}")
def training_job(job_id: str, runner: TrainingJobRunner = Depends(get_training_runner)) -> dict[str, Any]:
    """One job, with its progress curve."""
    job = runner.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"no such training job: {job_id}")
    return job.to_dict()


@router.post("/training/start")
def training_start(
    request: StartTrainingRequest,
    runner: TrainingJobRunner = Depends(get_training_runner),
) -> dict[str, Any]:
    """Start a training job in the background.

    Refused rather than queued when one is already running, when a simulation
    is running, or when the budget is above the configured cap. Each refusal
    says which it was — a silent queue would leave the operator watching a
    progress bar that belongs to somebody else's job.
    """
    try:
        job = runner.start(
            request.algorithm,
            timesteps=request.timesteps,
            seed=request.seed,
            evaluate_episodes=request.evaluate_episodes,
        )
    except TrainingUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except TrainingBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except TrainingRefusedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return job.to_dict()


@router.post("/training/stop")
def training_stop(runner: TrainingJobRunner = Depends(get_training_runner)) -> dict[str, Any]:
    """Ask the running job to stop at the next step boundary.

    The model trained so far is still saved and still usable; the run is
    recorded as CANCELLED rather than filed as a short completed one.
    """
    job = runner.stop()
    if job is None:
        raise HTTPException(status_code=409, detail="no training job is running")
    return job.to_dict()


# ------------------------------------------------------------------- PHASE 19


class EvaluateRequest(BaseModel):
    """How thoroughly to measure a saved policy."""

    episodes: int = Field(default=5, ge=1, le=50)
    seed: int | None = Field(default=None, description="Defaults to the simulation seed")


def _registry(settings: Settings) -> ModelRegistry:
    return ModelRegistry(settings)


def _model_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ModelNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    # 409: the model exists, but running it here would not mean anything.
    return HTTPException(status_code=409, detail=str(exc))


@router.get("/models")
def models_list(
    include_archived: bool = Query(default=False),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Every saved policy with a verdict on whether it still means anything."""
    from training.observation_encoder import LAYOUT_VERSION

    registry = _registry(settings)
    found = registry.list_models(include_archived=include_archived)
    return {
        "count": len(found),
        "models": found,
        # So an empty list can say which kind of empty it is.
        "archived_count": registry.archived_count(),
        "current_layout": LAYOUT_VERSION,
        "available": rl_available(),
        "install_hint": rl_status().install_hint,
        "unavailable_reason": rl_status().reason,
        "notice": (
            "A policy trained against a different observation layout still loads and still "
            "produces actions — from numbers that stopped meaning what they meant. Those are "
            "marked INCOMPATIBLE and cannot be evaluated."
        ),
    }


@router.get("/models/compare")
def models_compare(
    models: str = Query(description="Two or more model ids, comma separated"),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Line several policies up, and say whether lining them up means anything."""
    wanted = [m.strip() for m in models.split(",") if m.strip()]
    if len(wanted) < 2:
        raise HTTPException(status_code=400, detail="give at least two model ids to compare")
    if len(wanted) > 6:
        raise HTTPException(status_code=400, detail="compare at most six models at once")
    try:
        return _registry(settings).compare(wanted)
    except ModelNotFoundError as exc:
        raise _model_error(exc) from exc


@router.get("/models/{model_id}")
def model_detail(model_id: str, settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """One policy, its card and its verdict."""
    try:
        return _registry(settings).get(model_id)
    except ModelNotFoundError as exc:
        raise _model_error(exc) from exc


@router.post("/models/{model_id}/evaluate")
def model_evaluate(
    model_id: str,
    request: EvaluateRequest,
    runner: TrainingJobRunner = Depends(get_training_runner),
) -> dict[str, Any]:
    """Measure a saved policy, as a background job.

    Evaluation is not quick — an episode runs to the episode limit — so it goes
    through the same one-at-a-time runner as training rather than holding a
    request open for minutes.
    """
    try:
        job = runner.start_evaluation(model_id, episodes=request.episodes, seed=request.seed)
    except TrainingUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except TrainingBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except TrainingRefusedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (ModelNotFoundError, ModelIncompatibleError) as exc:
        raise _model_error(exc) from exc
    return job.to_dict()


@router.post("/models/{model_id}/archive")
def model_archive(model_id: str, settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Move a policy out of the active list, keeping the file and its card."""
    try:
        return _registry(settings).archive(model_id)
    except ModelNotFoundError as exc:
        raise _model_error(exc) from exc


@router.post("/models/{model_id}/restore")
def model_restore(model_id: str, settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Bring an archived policy back into the active list."""
    try:
        return _registry(settings).restore(model_id)
    except ModelNotFoundError as exc:
        raise _model_error(exc) from exc


@router.delete("/models/{model_id}")
def model_delete(model_id: str, settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Delete a policy and its card. Archiving is usually what is wanted."""
    try:
        return _registry(settings).delete(model_id)
    except ModelNotFoundError as exc:
        raise _model_error(exc) from exc
