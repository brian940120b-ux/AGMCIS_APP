"""Health and system-status endpoints."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends

from core.compute import detect_compute
from core.config import Settings, get_settings
from core.runtime import get_status_registry

router = APIRouter(tags=["health"])

# Process start time, used for uptime reporting.
_STARTED_AT = time.time()

# The build's phase, shown in the Command Center header. It went stale twice by
# being a literal nobody remembered to bump, so `test_health_api.py` now asserts
# it matches the last phase marked Complete in docs/PHASES.md. Written with a
# plain hyphen; the docs use an en dash and the test compares them accordingly.
BUILD_PHASE = "PHASE 14-15"


@router.get("/health")
def health(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Liveness probe — used by Docker, the frontend boot screen and CI."""
    return {
        "status": "ok",
        "app": settings.app_name,
        "title": settings.app_title,
        "version": settings.version,
        "uptime_s": round(time.time() - _STARTED_AT, 3),
        "config_hash": settings.config_hash,
        "timestamp": time.time(),
    }


@router.get("/system/status")
def system_status(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Subsystem states for the Command Center status panel.

    Subsystems that have not been built yet report NOT_IMPLEMENTED — this
    endpoint never reports a capability the backend does not have.
    """
    registry = get_status_registry()
    return {
        "operational": registry.operational,
        "phase": BUILD_PHASE,
        "config_hash": settings.config_hash,
        "subsystems": registry.to_list(),
    }


@router.get("/system/compute")
def system_compute() -> dict[str, Any]:
    """Report the CPU/GPU device available for simulation and training."""
    return detect_compute()


@router.get("/config")
def config_summary(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Expose the resolved configuration so the UI shows real values."""
    return {
        "config_hash": settings.config_hash,
        "simulation": {
            "tick_rate_hz": settings.simulation.tick_rate_hz,
            "dt": settings.simulation.dt,
            "allowed_speeds": settings.simulation.allowed_speeds,
            "default_speed": settings.simulation.default_speed,
            "deterministic": settings.simulation.deterministic,
            "seed": settings.simulation.seed,
            "max_duration_s": settings.simulation.max_duration_s,
        },
        "world": settings.world.model_dump(),
        "telemetry": settings.telemetry.model_dump(),
        "agents": settings.agents.model_dump(),
        "scenarios": settings.scenarios.model_dump(),
    }
