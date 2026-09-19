"""AIFCS backend application entry point.

    uvicorn main:app --reload --port 8000     (run from the backend/ directory)

AIFCS is a research, education and AI-training simulation platform. Every
entity, platform and parameter in it is fictional and abstract.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.agents import router as agents_router
from api.coordination import router as coordination_router
from api.health import router as health_router
from api.replay import router as replay_router
from api.scenarios import router as scenarios_router
from api.simulation import router as simulation_router
from api.telemetry import router as telemetry_router
from api.training import router as training_router
from core.config import APP_TITLE, Settings, get_settings
from core.logging_config import configure_logging, get_logger
from core.runtime import get_broadcaster, get_engine, get_run_manager, get_status_registry
from core.system_status import SubsystemState


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Configure logging on startup and announce the resolved configuration."""
    settings: Settings = get_settings()
    configure_logging(
        level=settings.logging.level,
        json_format=settings.logging.json_format,
        directory=settings.logging.directory,
        project_root=settings.project_root,
    )
    # Subsystems that genuinely run as of PHASE 3.
    registry = get_status_registry()
    registry.set_state("simulation", SubsystemState.ONLINE, "Fixed-timestep engine ready")
    registry.set_state("physics", SubsystemState.ONLINE, "Newton-Euler 6DOF, RK4 at the fixed timestep")
    registry.set_state("agents", SubsystemState.ONLINE, "Rule-based pilots deciding at the configured rate")
    registry.set_state(
        "controllers",
        SubsystemState.ONLINE,
        "Validation, envelope protection and actuator rate limiting",
    )
    registry.set_state(
        "sensors",
        SubsystemState.ONLINE,
        "Range, field of regard, noise, latency and dropout applied",
    )
    registry.set_state(
        "communications",
        SubsystemState.ONLINE,
        "Datalink with latency, jitter, loss, bandwidth and blackouts",
    )
    registry.set_state(
        "websocket",
        SubsystemState.ONLINE,
        "Pushing telemetry on /ws/simulation",
    )

    # PHASE 9. Each reports what its configuration actually enables, rather
    # than claiming to be online because the code exists.
    registry.set_state(
        "replay",
        SubsystemState.ONLINE if settings.replay.enabled else SubsystemState.OFFLINE,
        (
            f"Recording at {settings.replay.record_rate_hz:g} Hz to {settings.replay.directory}"
            if settings.replay.enabled
            else "Recording disabled in configs/analysis.yaml"
        ),
    )
    registry.set_state(
        "storage",
        SubsystemState.ONLINE if settings.storage.enabled else SubsystemState.OFFLINE,
        (
            f"SQLite run history at {settings.storage.database_path}"
            if settings.storage.enabled
            else "Run storage disabled in configs/analysis.yaml"
        ),
    )
    registry.set_state(
        "scoring",
        SubsystemState.ONLINE if settings.scoring.enabled else SubsystemState.OFFLINE,
        (
            f"Six weighted terms, {settings.scoring.weights.total():g} points available"
            if settings.scoring.enabled
            else "Scoring disabled in configs/analysis.yaml"
        ),
    )

    # PHASE 11-13. The environment and the pipelines are real; starting a run
    # from the browser is not, and the detail says so rather than implying a
    # button exists somewhere.
    from training.pipeline import resolve_device, rl_available

    if rl_available():
        registry.set_state(
            "training",
            SubsystemState.ONLINE,
            "Gymnasium env, PPO and SAC on "
            f"{resolve_device(settings.training.device)} - run from backend/train.py",
        )
    else:
        registry.set_state(
            "training",
            SubsystemState.OFFLINE,
            "RL stack not installed: pip install -r requirements-ml.txt",
        )

    # Opening the database here means a broken path fails at startup with a
    # clear message, rather than on the first run hours later.
    get_run_manager()

    # Telemetry pushes at its own rate, independent of the physics tick.
    get_broadcaster().start()

    log = get_logger("startup")
    log.info(
        "AIFCS backend online",
        extra={
            "event": "APP_STARTED",
            "version": settings.version,
            "config_hash": settings.config_hash,
            "tick_rate_hz": settings.simulation.tick_rate_hz,
            "deterministic": settings.simulation.deterministic,
        },
    )
    yield
    # Stop the telemetry and simulation loops cleanly so no task outlives us.
    await get_broadcaster().stop()
    await get_engine().stop()
    get_run_manager().finish_sync("backend shutting down")
    get_logger("shutdown").info("AIFCS backend offline", extra={"event": "APP_STOPPED"})


def create_app(settings: Settings | None = None) -> FastAPI:
    """Application factory — keeps the app testable and configurable."""
    settings = settings or get_settings()

    app = FastAPI(
        title=APP_TITLE,
        summary="Fictional multi-agent flight simulation platform for AI research.",
        version=settings.version,
        lifespan=lifespan,
    )

    # The Vite dev server runs on a different origin during development.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:4173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router, prefix="/api")
    app.include_router(simulation_router, prefix="/api")
    app.include_router(agents_router, prefix="/api")
    app.include_router(coordination_router, prefix="/api")
    app.include_router(replay_router, prefix="/api")
    app.include_router(scenarios_router, prefix="/api")
    app.include_router(training_router, prefix="/api")
    app.include_router(telemetry_router)

    @app.get("/", tags=["meta"])
    def root() -> dict[str, str]:
        return {
            "app": settings.app_name,
            "title": settings.app_title,
            "version": settings.version,
            "docs": "/docs",
            "health": "/api/health",
            "scope": "Research / education simulation platform. All entities are fictional.",
        }

    return app


app = create_app()
