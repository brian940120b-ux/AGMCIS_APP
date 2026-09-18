"""Scenario definition and loading (PHASE 1).

A scenario is a YAML file describing a fictional situation: which units exist,
where they start and how long the run lasts. The full editor arrives in
PHASE 10; this module is the loader the engine needs now.

Scenarios are validated before a run is accepted, so a typo fails at load time
rather than producing a silently wrong simulation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from core.world_state import EntityState, Environment, Team, WorldState


@dataclass
class ScenarioEntity:
    """One fictional flight unit as declared in a scenario file."""

    id: str
    team: Team = Team.NEUTRAL
    type: str = "fictional_aircraft"
    position: list[float] = field(default_factory=lambda: [0.0, 0.0, 5000.0])
    velocity: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    orientation: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    health: float = 1.0
    energy: float = 1.0
    fuel: float = 1.0

    def to_entity_state(self) -> EntityState:
        return EntityState(
            id=self.id,
            team=self.team,
            position=np.asarray(self.position, dtype=np.float64),
            velocity=np.asarray(self.velocity, dtype=np.float64),
            orientation=np.asarray(self.orientation, dtype=np.float64),
            health=self.health,
            energy=self.energy,
            fuel=self.fuel,
            metadata={"type": self.type},
        )


@dataclass
class Scenario:
    """A complete, validated scenario definition."""

    name: str
    description: str = ""
    version: str = "1.0"
    duration_s: float = 300.0
    seed: int | None = None
    entities: list[ScenarioEntity] = field(default_factory=list)
    environment: dict[str, Any] = field(default_factory=dict)

    def build_world(self) -> WorldState:
        """Create the initial truth state described by this scenario."""
        world = WorldState(
            environment=Environment(
                gravity_mps2=self.environment.get("gravity_mps2", 9.80665),
                air_density_kgpm3=self.environment.get("air_density_kgpm3", 1.225),
                wind=self.environment.get("wind", [0.0, 0.0, 0.0]),
            ),
            global_status={"scenario": self.name, "scenario_version": self.version},
        )
        for declared in self.entities:
            world.add_entity(declared.to_entity_state())
        return world

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "duration_s": self.duration_s,
            "seed": self.seed,
            "entity_count": len(self.entities),
            "entities": [
                {
                    "id": e.id,
                    "team": e.team.value,
                    "type": e.type,
                    "position": e.position,
                    "velocity": e.velocity,
                }
                for e in self.entities
            ],
            "environment": self.environment,
        }


class ScenarioError(ValueError):
    """Raised when a scenario file is missing or invalid."""


def parse_scenario(document: dict[str, Any]) -> Scenario:
    """Validate a parsed YAML document and build a Scenario."""
    if not isinstance(document, dict):
        raise ScenarioError("scenario file must contain a YAML mapping")

    header = document.get("scenario")
    if not isinstance(header, dict):
        raise ScenarioError("scenario file must contain a 'scenario' section")

    name = header.get("name")
    if not name:
        raise ScenarioError("scenario.name is required")

    duration = float(header.get("duration", 300.0))
    if duration <= 0:
        raise ScenarioError("scenario.duration must be positive")

    raw_entities = document.get("entities", [])
    if not isinstance(raw_entities, list) or not raw_entities:
        raise ScenarioError("scenario must declare at least one entity")

    entities: list[ScenarioEntity] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_entities):
        if not isinstance(raw, dict):
            raise ScenarioError(f"entity #{index} must be a mapping")

        entity_id = raw.get("id")
        if not entity_id:
            raise ScenarioError(f"entity #{index} is missing 'id'")
        if entity_id in seen:
            raise ScenarioError(f"duplicate entity id: {entity_id}")
        seen.add(entity_id)

        try:
            team = Team(str(raw.get("team", "NEUTRAL")).upper())
        except ValueError as exc:
            raise ScenarioError(f"entity {entity_id}: unknown team {raw.get('team')!r}") from exc

        for key in ("position", "velocity", "orientation"):
            value = raw.get(key)
            if value is not None and (not isinstance(value, list) or len(value) != 3):
                raise ScenarioError(f"entity {entity_id}: {key} must be a list of 3 numbers")

        entities.append(
            ScenarioEntity(
                id=entity_id,
                team=team,
                type=str(raw.get("type", "fictional_aircraft")),
                position=[float(v) for v in raw.get("position", [0.0, 0.0, 5000.0])],
                velocity=[float(v) for v in raw.get("velocity", [0.0, 0.0, 0.0])],
                orientation=[float(v) for v in raw.get("orientation", [0.0, 0.0, 0.0])],
                health=float(raw.get("health", 1.0)),
                energy=float(raw.get("energy", 1.0)),
                fuel=float(raw.get("fuel", 1.0)),
            )
        )

    seed = header.get("seed")
    return Scenario(
        name=str(name),
        description=str(header.get("description", "")),
        version=str(header.get("version", "1.0")),
        duration_s=duration,
        seed=int(seed) if seed is not None else None,
        entities=entities,
        environment=document.get("environment", {}) or {},
    )


def load_scenario(path: Path | str) -> Scenario:
    """Load and validate a scenario from a YAML file."""
    scenario_path = Path(path)
    if not scenario_path.is_file():
        raise ScenarioError(f"scenario file not found: {scenario_path}")

    with scenario_path.open("r", encoding="utf-8") as fh:
        document = yaml.safe_load(fh)
    return parse_scenario(document)


def list_scenarios(directory: Path | str) -> list[str]:
    """Names of every scenario file in a directory (without the extension)."""
    scenario_dir = Path(directory)
    if not scenario_dir.is_dir():
        return []
    return sorted(p.stem for p in scenario_dir.glob("*.yaml"))


def find_scenario(directory: Path | str, name: str) -> Scenario:
    """Load a scenario by name from a directory."""
    return load_scenario(Path(directory) / f"{name}.yaml")
