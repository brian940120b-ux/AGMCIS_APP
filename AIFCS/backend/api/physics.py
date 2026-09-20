"""Physics backend endpoints (PHASE 16).

Read-only. The backend is a configuration choice, not a runtime toggle: a run
is only reproducible from its ``config_hash``, and letting the model be swapped
underneath a running simulation would make the recording describe two different
worlds. Change ``physics.backend`` in ``configs/simulation.yaml`` and restart.

The endpoints exist so the dashboard can state which model is flying, and say
honestly when the configured one is not installed.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from core.config import Settings, get_settings
from core.physics_backend import backend_status
from core.runtime import get_engine
from core.simulation_engine import SimulationEngine

router = APIRouter(tags=["physics"])


@router.get("/physics")
def physics(
    settings: Settings = Depends(get_settings),
    engine: SimulationEngine = Depends(get_engine),
) -> dict[str, Any]:
    """Which backend is configured, which is running, and what else exists."""
    return backend_status(settings, engine.integrator)


@router.get("/physics/airframes")
def airframes() -> dict[str, Any]:
    """The fictional platforms both backends fly.

    Both read these same parameters — the JSBSim aircraft files are generated
    from them — so this is one list, not one per backend.
    """
    from simulation.aircraft import FICTIONAL_AIRCRAFT

    return {
        "count": len(FICTIONAL_AIRCRAFT),
        "airframes": [
            {
                "name": name,
                "mass_kg": params.mass_kg,
                "wing_area_m2": params.wing_area_m2,
                "wing_span_m": params.wing_span_m,
                "mean_chord_m": params.mean_chord_m,
                "max_thrust_n": params.max_thrust_n,
                "inertia": [params.inertia_xx, params.inertia_yy, params.inertia_zz],
            }
            for name, params in sorted(FICTIONAL_AIRCRAFT.items())
        ],
        "notice": (
            "Every airframe is fictional and abstract, invented for simulation "
            "research. No real aircraft data is used or modelled."
        ),
    }
