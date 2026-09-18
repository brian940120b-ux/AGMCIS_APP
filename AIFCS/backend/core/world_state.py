"""World state — the authoritative truth of the simulation (PHASE 1).

This is the **truth state**. Agents never write to it: they receive an
``Observation`` derived from it (PHASE 5) and return an ``Action``. Only the
physics integrator, driven by the engine, mutates entities.

All platforms are fictional and abstract (``BLUE-01``, ``RED-02``). Nothing here
models a real aircraft, sensor or weapon.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import numpy as np


class Team(StrEnum):
    BLUE = "BLUE"
    RED = "RED"
    NEUTRAL = "NEUTRAL"


class EntityStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    OUT_OF_BOUNDS = "OUT_OF_BOUNDS"
    DISABLED = "DISABLED"


def _vec3(values: Any) -> np.ndarray:
    """Coerce input into a float64 3-vector, rejecting NaN and Inf."""
    array = np.asarray(values, dtype=np.float64).reshape(3)
    if not np.all(np.isfinite(array)):
        raise ValueError(f"vector must be finite, got {values!r}")
    return array


@dataclass
class EntityState:
    """State of one fictional flight unit.

    Position is metres in a local tangent plane: +X east, +Y north, +Z up.
    Orientation is (roll, pitch, yaw) in radians.
    """

    id: str
    team: Team = Team.NEUTRAL
    position: np.ndarray = field(default_factory=lambda: np.zeros(3))
    velocity: np.ndarray = field(default_factory=lambda: np.zeros(3))
    orientation: np.ndarray = field(default_factory=lambda: np.zeros(3))
    angular_velocity: np.ndarray = field(default_factory=lambda: np.zeros(3))

    health: float = 1.0
    energy: float = 1.0
    fuel: float = 1.0

    # Populated by the sensor and communication models in PHASE 5 / PHASE 6.
    sensor_state: dict[str, Any] = field(default_factory=dict)
    communication_state: dict[str, Any] = field(default_factory=dict)

    status: EntityStatus = EntityStatus.ACTIVE
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("entity id must not be empty")
        self.team = Team(self.team)
        self.status = EntityStatus(self.status)
        self.position = _vec3(self.position)
        self.velocity = _vec3(self.velocity)
        self.orientation = _vec3(self.orientation)
        self.angular_velocity = _vec3(self.angular_velocity)

    # ------------------------------------------------------------ properties

    @property
    def altitude(self) -> float:
        """Height above the scenario datum, in metres."""
        return float(self.position[2])

    @property
    def speed(self) -> float:
        """Magnitude of the velocity vector, in m/s."""
        return float(np.linalg.norm(self.velocity))

    @property
    def heading_deg(self) -> float:
        """Compass heading in degrees: 0 = north, 90 = east."""
        east, north = float(self.velocity[0]), float(self.velocity[1])
        if east == 0.0 and north == 0.0:
            # Stationary: fall back to the yaw the platform is pointing at.
            return float(np.degrees(self.orientation[2]) % 360.0)
        return float(np.degrees(np.arctan2(east, north)) % 360.0)

    def copy(self) -> EntityState:
        """Deep copy — replay and determinism checks must not alias arrays."""
        return EntityState(
            id=self.id,
            team=self.team,
            position=self.position.copy(),
            velocity=self.velocity.copy(),
            orientation=self.orientation.copy(),
            angular_velocity=self.angular_velocity.copy(),
            health=self.health,
            energy=self.energy,
            fuel=self.fuel,
            sensor_state=dict(self.sensor_state),
            communication_state=dict(self.communication_state),
            status=self.status,
            metadata=dict(self.metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "team": self.team.value,
            "position": self.position.tolist(),
            "velocity": self.velocity.tolist(),
            "orientation": self.orientation.tolist(),
            "angular_velocity": self.angular_velocity.tolist(),
            "altitude": self.altitude,
            "speed": self.speed,
            "heading_deg": self.heading_deg,
            "health": self.health,
            "energy": self.energy,
            "fuel": self.fuel,
            "status": self.status.value,
            "sensor_state": self.sensor_state,
            "communication_state": self.communication_state,
            "metadata": self.metadata,
        }


@dataclass
class Environment:
    """Abstract environment parameters shared by every entity."""

    gravity_mps2: float = 9.80665
    air_density_kgpm3: float = 1.225
    wind: np.ndarray = field(default_factory=lambda: np.zeros(3))

    def __post_init__(self) -> None:
        self.wind = _vec3(self.wind)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gravity_mps2": self.gravity_mps2,
            "air_density_kgpm3": self.air_density_kgpm3,
            "wind": self.wind.tolist(),
        }


@dataclass
class WorldState:
    """Complete truth state at one instant of simulation time."""

    simulation_time: float = 0.0
    tick: int = 0
    environment: Environment = field(default_factory=Environment)
    entities: dict[str, EntityState] = field(default_factory=dict)
    global_status: dict[str, Any] = field(default_factory=dict)

    # -------------------------------------------------------------- entities

    def add_entity(self, entity: EntityState) -> None:
        if entity.id in self.entities:
            raise ValueError(f"duplicate entity id: {entity.id}")
        self.entities[entity.id] = entity

    def get(self, entity_id: str) -> EntityState | None:
        return self.entities.get(entity_id)

    def team_entities(self, team: Team) -> list[EntityState]:
        return [e for e in self.entities.values() if e.team is team]

    @property
    def active_entities(self) -> list[EntityState]:
        return [e for e in self.entities.values() if e.status is EntityStatus.ACTIVE]

    # --------------------------------------------------------------- copying

    def snapshot(self) -> WorldState:
        """Independent deep copy, safe to hand to the replay recorder."""
        return WorldState(
            simulation_time=self.simulation_time,
            tick=self.tick,
            environment=Environment(
                gravity_mps2=self.environment.gravity_mps2,
                air_density_kgpm3=self.environment.air_density_kgpm3,
                wind=self.environment.wind.copy(),
            ),
            entities={eid: e.copy() for eid, e in self.entities.items()},
            global_status=dict(self.global_status),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "simulation_time": self.simulation_time,
            "tick": self.tick,
            "environment": self.environment.to_dict(),
            "entities": [e.to_dict() for e in self.entities.values()],
            "entity_count": len(self.entities),
            "global_status": self.global_status,
        }

    @property
    def state_hash(self) -> str:
        """Digest of the physical state, used to prove two runs are identical.

        Values are rounded to 9 decimals so that bit-level float noise from an
        unrelated code path does not mask a genuine determinism check.
        """
        payload = [
            [
                entity.id,
                entity.team.value,
                entity.status.value,
                [round(v, 9) for v in entity.position.tolist()],
                [round(v, 9) for v in entity.velocity.tolist()],
                [round(v, 9) for v in entity.orientation.tolist()],
                [round(v, 9) for v in entity.angular_velocity.tolist()],
                round(entity.health, 9),
                round(entity.energy, 9),
                round(entity.fuel, 9),
            ]
            for entity in sorted(self.entities.values(), key=lambda e: e.id)
        ]
        blob = json.dumps([self.tick, round(self.simulation_time, 9), payload], sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
