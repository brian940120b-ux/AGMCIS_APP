"""Training and model endpoints (PHASE 11-13).

Read-only. Training itself is started from the command line
(``backend/train.py``), not from here: a long job needs progress reporting,
cancellation and survival across a page reload, and that job management is the
training centre's work in a later phase. Putting a START TRAINING button in the
dashboard now would be a control that cannot do what it appears to.

So this router answers what *is* true today: which device is available, what
the environment looks like, what the reward weights are, which policies have
been trained, and how they scored.

The RL stack is an optional dependency. Every endpoint works without it and
says plainly that it is missing, rather than failing to import.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from core.config import Settings, get_settings
from core.logging_config import get_logger
from core.run_manager import RunManager
from core.runtime import get_run_manager
from training.pipeline import ALGORITHMS, TrainingPipeline, resolve_device, rl_available
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
    available = rl_available()
    training = settings.training
    return {
        "available": available,
        "install_hint": None if available else INSTALL_HINT,
        "how_to_run": CLI_HINT,
        "browser_control": False,
        "browser_control_note": (
            "Training is started from the command line. Driving a long job from the "
            "dashboard needs progress, cancellation and reload survival, which arrives "
            "with the training centre."
        ),
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
