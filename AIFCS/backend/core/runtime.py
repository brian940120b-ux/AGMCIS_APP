"""Process-wide runtime singletons.

Kept in their own module so API routers never have to import ``main``, which
would be circular. ``main`` wires these up at startup.
"""

from __future__ import annotations

from functools import lru_cache

from core.config import get_settings
from core.run_manager import RunManager
from core.simulation_engine import SimulationEngine
from core.system_status import SystemStatusRegistry, build_default_registry
from core.telemetry import TelemetryBroadcaster
from replay.player import ReplayPlayer


@lru_cache(maxsize=1)
def get_status_registry() -> SystemStatusRegistry:
    return build_default_registry()


@lru_cache(maxsize=1)
def get_engine() -> SimulationEngine:
    """The single simulation engine instance shared by every request."""
    return SimulationEngine()


@lru_cache(maxsize=1)
def get_broadcaster() -> TelemetryBroadcaster:
    """The telemetry broadcaster shared by every WebSocket connection."""
    return TelemetryBroadcaster(
        engine=get_engine(),
        broadcast_rate_hz=get_settings().telemetry.broadcast_rate_hz,
    )


@lru_cache(maxsize=1)
def get_run_manager() -> RunManager:
    """Records, stores and scores runs (PHASE 9)."""
    return RunManager()


@lru_cache(maxsize=1)
def get_replay_player() -> ReplayPlayer:
    """The shared replay cursor.

    One player process-wide, for the same reason there is one engine: every
    view should agree on where the playback is.
    """
    return ReplayPlayer(allowed_speeds=tuple(get_settings().simulation.allowed_speeds))


def reset_runtime() -> None:
    """Drop cached singletons — used by tests to get a clean engine."""
    get_status_registry.cache_clear()
    get_engine.cache_clear()
    get_broadcaster.cache_clear()
    get_run_manager.cache_clear()
    get_replay_player.cache_clear()
