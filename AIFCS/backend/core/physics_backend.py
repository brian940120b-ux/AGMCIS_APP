"""Choosing the physics backend (PHASE 16).

PHASE 1 declared the ``Integrator`` protocol so the physics could be swapped.
Until now that swap was only reachable by passing an instance to the engine's
constructor, which meant it was reachable from tests and from nowhere else.
This module makes it a configuration choice, and makes the platform able to
*say* what it is flying with.

The rule is that a backend never quietly becomes a different one. Asking for
JSBSim without the package installed raises an error naming what to install; it
does not fall back to ``simple_6dof`` and leave the run looking as if the
requested model had been used. A run recorded under the wrong model is worse
than a run that refused to start.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.config import Settings
from core.integrator import Integrator, KinematicIntegrator, NullIntegrator
from core.logging_config import get_logger
from simulation.jsbsim_adapter import (
    INSTALL_HINT,
    GeodeticReference,
    JSBSimAdapter,
    JSBSimUnavailableError,
    jsbsim_available,
    jsbsim_version,
)
from simulation.physics import Simple6DOFModel

log = get_logger("physics.backend")


@dataclass(frozen=True)
class BackendInfo:
    """What one backend is and whether it can actually be used right now."""

    key: str
    title: str
    description: str
    available: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "description": self.description,
            "available": self.available,
            "detail": self.detail,
        }


def available_backends() -> list[BackendInfo]:
    """Every backend, with an honest account of which ones are installed."""
    version = jsbsim_version()
    return [
        BackendInfo(
            key="simple_6dof",
            title="Simple 6DOF",
            description=(
                "This platform's Newton-Euler rigid-body model: quaternion attitude, "
                "RK4 at the fixed timestep, constant-density atmosphere from config."
            ),
            available=True,
            detail="Newton-Euler 6DOF, RK4 at the fixed timestep",
        ),
        BackendInfo(
            key="jsbsim",
            title="JSBSim",
            description=(
                "The JSBSim flight dynamics model, flying airframes generated from the "
                "same fictional AircraftParameters. Models a standard atmosphere, so "
                "density falls with altitude."
            ),
            available=jsbsim_available(),
            detail=(f"JSBSim {version}" if version else "Not installed \u2014 pip install jsbsim"),
        ),
        BackendInfo(
            key="kinematic",
            title="Kinematic",
            description="Constant velocity. Real integration, but no forces at all.",
            available=True,
            detail="Constant velocity — for isolating a problem from the aerodynamics",
        ),
        BackendInfo(
            key="null",
            title="Frozen",
            description="Nothing moves. Used to hold a scenario still.",
            available=True,
            detail="Nothing is integrated",
        ),
    ]


def build_integrator(settings: Settings) -> Integrator:
    """Build the integrator named by ``physics.backend``.

    Raises ``JSBSimUnavailableError`` when JSBSim is asked for and absent — the
    run does not start rather than starting under a model nobody chose.
    """
    backend = settings.physics.backend

    if backend == "simple_6dof":
        return Simple6DOFModel()
    if backend == "kinematic":
        return KinematicIntegrator()
    if backend == "null":
        return NullIntegrator()
    if backend == "jsbsim":
        if not jsbsim_available():
            raise JSBSimUnavailableError(INSTALL_HINT)
        jsb = settings.physics.jsbsim
        root = Path(jsb.data_root)
        if not root.is_absolute():
            root = settings.project_root / root
        adapter = JSBSimAdapter(
            data_root=root,
            reference=GeodeticReference(
                latitude_deg=jsb.reference_latitude_deg,
                longitude_deg=jsb.reference_longitude_deg,
            ),
        )
        log.info(
            "physics backend selected",
            extra={"event": "PHYSICS_BACKEND", "backend": backend, "version": jsbsim_version()},
        )
        return adapter

    # Unreachable: the config validator rejects anything else. Kept so a future
    # backend added to the validator and forgotten here fails loudly.
    raise ValueError(f"no builder for physics backend {backend!r}")


def backend_status(settings: Settings, active: Integrator | None = None) -> dict[str, Any]:
    """What the API and the dashboard report about the physics backend."""
    requested = settings.physics.backend
    backends = available_backends()
    chosen = next((b for b in backends if b.key == requested), None)

    status: dict[str, Any] = {
        "requested": requested,
        "active": active.name if active is not None else None,
        "available": chosen.available if chosen else False,
        "detail": chosen.detail if chosen else f"unknown backend {requested!r}",
        "backends": [b.to_dict() for b in backends],
        "notice": (
            "Every airframe is fictional. The JSBSim backend flies aircraft files "
            "generated from this platform's own AircraftParameters; JSBSim's bundled "
            "real aircraft are never loaded."
        ),
    }
    if requested == "jsbsim" and not status["available"]:
        status["install_hint"] = INSTALL_HINT
    if active is not None and hasattr(active, "status"):
        status["backend_status"] = active.status()
    return status
