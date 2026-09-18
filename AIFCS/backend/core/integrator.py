"""Motion integrator interface (PHASE 1).

The engine never integrates motion itself — it delegates to an ``Integrator``.
That is what lets the physics backend be swapped (``Simple6DOFModel`` in
PHASE 2, ``JSBSimAdapter`` in PHASE 16) without touching the engine.

PHASE 1 ships ``KinematicIntegrator``: straight-line constant-velocity motion.
That is real integration, but it models **no forces at all** — no gravity, drag,
lift or thrust. PHASE 2 replaces it with Newton-Euler rigid-body dynamics.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np

from core.world_state import EntityState, EntityStatus, WorldState


@runtime_checkable
class Integrator(Protocol):
    """Advances every entity by exactly one fixed timestep."""

    name: str

    def integrate(self, world: WorldState, dt: float) -> None: ...


class NullIntegrator:
    """Advances nothing. Used when a scenario should hold entities frozen."""

    name = "null"

    def integrate(self, world: WorldState, dt: float) -> None:
        return None


class KinematicIntegrator:
    """Constant-velocity motion: ``position += velocity * dt``.

    Deterministic and allocation-free per entity, so repeated runs with the same
    initial state produce bit-identical trajectories.
    """

    name = "kinematic"

    def integrate(self, world: WorldState, dt: float) -> None:
        for entity in world.entities.values():
            if entity.status is not EntityStatus.ACTIVE:
                continue
            entity.position += entity.velocity * dt
            entity.orientation += entity.angular_velocity * dt
            # Keep yaw/roll/pitch in (-pi, pi] so long runs cannot drift upward
            # without bound and lose float precision.
            entity.orientation = np.mod(entity.orientation + np.pi, 2 * np.pi) - np.pi


def clamp_to_bounds(entity: EntityState, bounds: dict[str, float]) -> bool:
    """Clamp an entity inside the world volume.

    Returns True when the entity had to be clamped, so the engine can raise an
    ENTITY_OUT_OF_BOUNDS event. The entity is stopped rather than deleted: a
    research platform should show what happened, not silently drop the unit.
    """
    limits = (
        (0, bounds["x_min"], bounds["x_max"]),
        (1, bounds["y_min"], bounds["y_max"]),
        (2, bounds["altitude_min"], bounds["altitude_max"]),
    )

    breached = False
    for axis, low, high in limits:
        if entity.position[axis] < low:
            entity.position[axis] = low
            entity.velocity[axis] = 0.0
            breached = True
        elif entity.position[axis] > high:
            entity.position[axis] = high
            entity.velocity[axis] = 0.0
            breached = True

    if breached:
        entity.status = EntityStatus.OUT_OF_BOUNDS
    return breached
