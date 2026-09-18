"""Process-wide runtime singletons.

Kept in their own module so API routers never have to import ``main``, which
would be circular. ``main`` wires these up at startup.
"""

from __future__ import annotations

from functools import lru_cache

from core.simulation_engine import SimulationEngine
from core.system_status import SystemStatusRegistry, build_default_registry


@lru_cache(maxsize=1)
def get_status_registry() -> SystemStatusRegistry:
    return build_default_registry()


@lru_cache(maxsize=1)
def get_engine() -> SimulationEngine:
    """The single simulation engine instance shared by every request."""
    return SimulationEngine()


def reset_runtime() -> None:
    """Drop cached singletons — used by tests to get a clean engine."""
    get_status_registry.cache_clear()
    get_engine.cache_clear()
