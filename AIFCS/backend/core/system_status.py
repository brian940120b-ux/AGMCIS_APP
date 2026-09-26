"""Subsystem status registry.

The Command Center status panel reads this. A subsystem reports ONLINE only
when the module behind it actually runs; anything still to be built reports
NOT_IMPLEMENTED. No placeholder ever claims to be online (see project rule:
"do not produce fake features").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class SubsystemState(StrEnum):
    ONLINE = "ONLINE"
    READY = "READY"
    WARNING = "WARNING"
    ERROR = "ERROR"
    OFFLINE = "OFFLINE"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


@dataclass
class Subsystem:
    key: str
    label: str
    state: SubsystemState
    detail: str = ""
    phase: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "state": self.state.value,
            "detail": self.detail,
            "phase": self.phase,
            "metadata": self.metadata,
        }


class SystemStatusRegistry:
    """Mutable registry of subsystem states, updated as phases land."""

    def __init__(self) -> None:
        self._subsystems: dict[str, Subsystem] = {}

    def register(self, subsystem: Subsystem) -> None:
        self._subsystems[subsystem.key] = subsystem

    def set_state(self, key: str, state: SubsystemState, detail: str = "") -> None:
        sub = self._subsystems.get(key)
        if sub is None:
            raise KeyError(f"unknown subsystem: {key}")
        sub.state = state
        if detail:
            sub.detail = detail

    def get(self, key: str) -> Subsystem | None:
        return self._subsystems.get(key)

    def all(self) -> list[Subsystem]:
        return list(self._subsystems.values())

    def to_list(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in self._subsystems.values()]

    @property
    def operational(self) -> bool:
        """True when nothing that exists is in an error state."""
        return not any(s.state is SubsystemState.ERROR for s in self._subsystems.values())


def build_default_registry() -> SystemStatusRegistry:
    """Status of every planned subsystem as of the current phase."""
    registry = SystemStatusRegistry()
    ni = SubsystemState.NOT_IMPLEMENTED
    online = SubsystemState.ONLINE

    registry.register(Subsystem("api", "REST API", online, "FastAPI application serving", "PHASE 0"))
    registry.register(Subsystem("config", "Configuration", online, "YAML configs loaded", "PHASE 0"))
    registry.register(Subsystem("logging", "Logging", online, "Structured JSON logging active", "PHASE 0"))
    registry.register(Subsystem("simulation", "Simulation Engine", ni, "Scheduled for PHASE 1", "PHASE 1"))
    registry.register(Subsystem("physics", "Physics Engine", ni, "Scheduled for PHASE 2", "PHASE 2"))
    registry.register(Subsystem("agents", "Agent Manager", ni, "Scheduled for PHASE 3", "PHASE 3"))
    registry.register(Subsystem("controllers", "Flight Controller", ni, "Scheduled for PHASE 4", "PHASE 4"))
    registry.register(Subsystem("sensors", "Sensor Model", ni, "Scheduled for PHASE 5", "PHASE 5"))
    registry.register(Subsystem("communications", "Comm Model", ni, "Scheduled for PHASE 6", "PHASE 6"))
    registry.register(Subsystem("websocket", "WebSocket Telemetry", ni, "Scheduled for PHASE 7", "PHASE 7"))
    registry.register(Subsystem("replay", "Replay Engine", ni, "Scheduled for PHASE 9", "PHASE 9"))
    registry.register(Subsystem("scoring", "Scoring Engine", ni, "Scheduled for PHASE 9", "PHASE 9"))
    registry.register(Subsystem("storage", "Database", ni, "Scheduled for PHASE 9", "PHASE 9"))
    registry.register(Subsystem("training", "Training Engine", ni, "Scheduled for PHASE 11", "PHASE 11"))
    registry.register(Subsystem("dashboard", "Dashboard", ni, "Scheduled for COMP PHASE 10", "COMP PHASE 10"))
    return registry
