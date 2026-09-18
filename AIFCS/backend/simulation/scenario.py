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
from simulation.aircraft import FICTIONAL_AIRCRAFT, ControlInputs


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
    controls: ControlInputs = field(default_factory=ControlInputs)

    # Agent assignment (PHASE 3). "rule" flies it, "none" leaves it unpiloted.
    agent: str | None = None
    waypoints: list[list[float]] = field(default_factory=list)
    route_loop: bool = True
    formation_leader: str | None = None
    formation_offset: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])

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
            controls=self.controls,
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
                    "controls": e.controls.to_dict(),
                    "agent": e.agent,
                    "waypoints": e.waypoints,
                    "formation_leader": e.formation_leader,
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

        aircraft_type = str(raw.get("type", "fictional_aircraft"))
        if aircraft_type not in FICTIONAL_AIRCRAFT:
            raise ScenarioError(
                f"entity {entity_id}: unknown aircraft type {aircraft_type!r}; "
                f"available: {sorted(FICTIONAL_AIRCRAFT)}"
            )

        raw_controls = raw.get("controls", {}) or {}
        if not isinstance(raw_controls, dict):
            raise ScenarioError(f"entity {entity_id}: controls must be a mapping")

        agent_type = raw.get("agent")
        if agent_type is not None and agent_type not in {"rule", "none"}:
            raise ScenarioError(
                f"entity {entity_id}: unknown agent type {agent_type!r}; expected 'rule' or 'none'"
            )

        raw_waypoints = raw.get("waypoints", []) or []
        if not isinstance(raw_waypoints, list):
            raise ScenarioError(f"entity {entity_id}: waypoints must be a list")
        waypoints: list[list[float]] = []
        for w_index, waypoint in enumerate(raw_waypoints):
            if not isinstance(waypoint, list) or len(waypoint) != 3:
                raise ScenarioError(f"entity {entity_id}: waypoint #{w_index} must be a list of 3 numbers")
            waypoints.append([float(v) for v in waypoint])

        raw_formation = raw.get("formation", {}) or {}
        if not isinstance(raw_formation, dict):
            raise ScenarioError(f"entity {entity_id}: formation must be a mapping")
        formation_leader = raw_formation.get("leader")
        formation_offset = raw_formation.get("offset", [0.0, 0.0, 0.0])
        if formation_leader is not None and (
            not isinstance(formation_offset, list) or len(formation_offset) != 3
        ):
            raise ScenarioError(f"entity {entity_id}: formation.offset must be a list of 3 numbers")

        entities.append(
            ScenarioEntity(
                id=entity_id,
                team=team,
                type=aircraft_type,
                position=[float(v) for v in raw.get("position", [0.0, 0.0, 5000.0])],
                velocity=[float(v) for v in raw.get("velocity", [0.0, 0.0, 0.0])],
                orientation=[float(v) for v in raw.get("orientation", [0.0, 0.0, 0.0])],
                health=float(raw.get("health", 1.0)),
                energy=float(raw.get("energy", 1.0)),
                fuel=float(raw.get("fuel", 1.0)),
                controls=ControlInputs(
                    aileron=float(raw_controls.get("aileron", 0.0)),
                    elevator=float(raw_controls.get("elevator", 0.0)),
                    rudder=float(raw_controls.get("rudder", 0.0)),
                    throttle=float(raw_controls.get("throttle", 0.0)),
                ).clamped(),
                agent=agent_type,
                waypoints=waypoints,
                route_loop=bool(raw.get("route_loop", True)),
                formation_leader=formation_leader,
                formation_offset=[float(v) for v in formation_offset],
            )
        )

    declared_ids = {e.id for e in entities}
    for declared in entities:
        if declared.formation_leader and declared.formation_leader not in declared_ids:
            raise ScenarioError(
                f"entity {declared.id}: formation leader {declared.formation_leader!r} "
                "is not declared in this scenario"
            )
        if declared.formation_leader == declared.id:
            raise ScenarioError(f"entity {declared.id}: cannot be its own formation leader")

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
