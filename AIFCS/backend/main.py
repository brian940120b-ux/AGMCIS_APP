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

from api.health import router as health_router
from api.simulation import router as simulation_router
from core.config import APP_TITLE, Settings, get_settings
from core.logging_config import configure_logging, get_logger
from core.runtime import get_engine, get_status_registry
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
    # Subsystems that genuinely run as of PHASE 1.
    registry = get_status_registry()
    registry.set_state("simulation", SubsystemState.ONLINE, "Fixed-timestep engine ready")
    registry.set_state("physics", SubsystemState.WARNING, "Kinematic integrator only — 6DOF lands in PHASE 2")

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
    # Stop the simulation loop cleanly so no task outlives the process.
    await get_engine().stop()
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
