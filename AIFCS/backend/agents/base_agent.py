"""Agent contract (PHASE 3).

Every agent — rule-based, behaviour tree, RL or commander — follows the same
cycle: ``observe → think → act``, with ``update`` and ``reset`` around it.

The pipeline direction is fixed and one-way:

    Truth → Observation → Decision → Action → Controller → Physics

An agent receives an ``Observation`` and returns an ``Action``. It never holds a
reference to the truth state and cannot write to the world. In PHASE 3 the
observation is still built from truth without degradation; PHASE 5 inserts the
sensor model at exactly that boundary, and no agent code has to change.

Every decision carries **reason codes derived from measured state**. Nothing
here invents an explanation: a reason code is emitted only when the condition it
names was actually computed and met.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import numpy as np

from core.world_state import Team
from simulation.aircraft import ControlInputs


class Behaviour(StrEnum):
    """High-level intent an agent can select."""

    HOLD = "HOLD"
    NAVIGATE = "NAVIGATE"
    PATROL = "PATROL"
    FORMATION = "FORMATION"
    AVOID = "AVOID"
    # A learned policy chose the controls directly (PHASE 11). Kept distinct
    # from the rule behaviours so the decision feed never implies a policy
    # reasoned its way to NAVIGATE when it did not.
    POLICY = "POLICY"


class ReasonCode(StrEnum):
    """Why a behaviour was selected. Each maps to a computed condition."""

    NO_ROUTE_ASSIGNED = "NO_ROUTE_ASSIGNED"
    WAYPOINT_ACTIVE = "WAYPOINT_ACTIVE"
    WAYPOINT_REACHED = "WAYPOINT_REACHED"
    ROUTE_COMPLETE = "ROUTE_COMPLETE"
    FORMATION_ASSIGNED = "FORMATION_ASSIGNED"
    FORMATION_SEPARATION_HIGH = "FORMATION_SEPARATION_HIGH"
    FORMATION_IN_STATION = "FORMATION_IN_STATION"
    LEADER_UNAVAILABLE = "LEADER_UNAVAILABLE"
    COLLISION_RISK = "COLLISION_RISK"
    ALTITUDE_BELOW_TARGET = "ALTITUDE_BELOW_TARGET"
    ALTITUDE_ABOVE_TARGET = "ALTITUDE_ABOVE_TARGET"
    HEADING_ERROR_LARGE = "HEADING_ERROR_LARGE"
    SPEED_BELOW_TARGET = "SPEED_BELOW_TARGET"
    SPEED_ABOVE_TARGET = "SPEED_ABOVE_TARGET"

    # PHASE 11. A policy's controls come from a network, not from a rule, and
    # saying so is the honest reason code. The measured codes above are still
    # emitted alongside it, so the feed shows the conditions the policy acted
    # under even though they did not cause the action.
    POLICY_ACTION = "POLICY_ACTION"


@dataclass(frozen=True)
class ContactView:
    """What one agent perceives of another entity.

    This is an **estimate**, not truth. From PHASE 5 the values carry sensor
    noise, the position may be up to ``age_s`` seconds stale, and ``measured``
    distinguishes a fresh detection from a track being coasted through a
    dropout.
    """

    entity_id: str
    team: Team
    relative_position: np.ndarray
    relative_velocity: np.ndarray
    distance_m: float
    is_friendly: bool
    # Seconds since this contact was last actually measured. 0.0 when fresh.
    age_s: float = 0.0
    # Track quality, derived from range and staleness.
    confidence: float = 1.0
    # False when the track is being coasted rather than measured this cycle.
    measured: bool = True
    # Where this contact came from: this aircraft's own sensor, or a teammate's
    # report relayed over the datalink (PHASE 6).
    source: str = "SENSOR"

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "team": self.team.value,
            "relative_position": self.relative_position.tolist(),
            "distance_m": round(self.distance_m, 1),
            "is_friendly": self.is_friendly,
            "age_s": round(self.age_s, 3),
            "confidence": round(self.confidence, 3),
            "measured": self.measured,
            "source": self.source,
        }


@dataclass(frozen=True)
class Observation:
    """What an agent knows at one decision step.

    PHASE 3 fills this from truth directly. PHASE 5 replaces the construction
    with a sensor model that adds noise, delay and dropout — the shape of this
    object is the contract that makes that swap invisible to agents.
    """

    agent_id: str
    entity_id: str
    team: Team
    simulation_time: float

    position: np.ndarray
    velocity: np.ndarray
    orientation: np.ndarray
    angular_velocity: np.ndarray
    altitude: float
    speed: float
    heading_deg: float

    contacts: list[ContactView] = field(default_factory=list)
    # Mean track quality across the contacts. 1.0 when nothing is being tracked
    # or when the sensor model is disabled.
    confidence: float = 1.0

    @property
    def friendlies(self) -> list[ContactView]:
        return [c for c in self.contacts if c.is_friendly]

    @property
    def nearest_contact(self) -> ContactView | None:
        return min(self.contacts, key=lambda c: c.distance_m, default=None)

    def summary(self) -> dict[str, Any]:
        """Compact form recorded alongside a decision, for explainability."""
        nearest = self.nearest_contact
        return {
            "altitude_m": round(self.altitude, 1),
            "speed_mps": round(self.speed, 1),
            "heading_deg": round(self.heading_deg, 1),
            "contacts": len(self.contacts),
            "measured_contacts": sum(1 for c in self.contacts if c.measured),
            "datalink_contacts": sum(1 for c in self.contacts if c.source == "DATALINK"),
            "nearest_contact_m": round(nearest.distance_m, 1) if nearest else None,
            "max_track_age_s": round(max((c.age_s for c in self.contacts), default=0.0), 2),
            "confidence": round(self.confidence, 3),
        }


@dataclass(frozen=True)
class Action:
    """What an agent commands.

    PHASE 3 agents emit control demands directly. PHASE 4 inserts the action
    validator and command mapper between this and the physics.
    """

    controls: ControlInputs
    target_altitude_m: float | None = None
    target_speed_mps: float | None = None
    target_heading_deg: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "controls": self.controls.to_dict(),
            "target_altitude_m": self.target_altitude_m,
            "target_speed_mps": self.target_speed_mps,
            "target_heading_deg": self.target_heading_deg,
        }


@dataclass(frozen=True)
class Decision:
    """A behaviour choice plus the evidence behind it."""

    agent_id: str
    entity_id: str
    simulation_time: float
    tick: int
    behaviour: Behaviour
    confidence: float
    reason_codes: list[ReasonCode]
    observation_summary: dict[str, Any]
    metrics: dict[str, Any] = field(default_factory=dict)
    # Assigned by the agent manager, monotonic for the run, so a telemetry
    # client can request everything after a sequence it has already seen.
    sequence: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "agent_id": self.agent_id,
            "entity_id": self.entity_id,
            "simulation_time": round(self.simulation_time, 3),
            "tick": self.tick,
            "behaviour": self.behaviour.value,
            "confidence": round(self.confidence, 3),
            "reason_codes": [code.value for code in self.reason_codes],
            "observation": self.observation_summary,
            "metrics": self.metrics,
        }


class BaseAgent(ABC):
    """Common lifecycle for every AIFCS agent."""

    def __init__(self, agent_id: str, entity_id: str, team: Team) -> None:
        self.agent_id = agent_id
        self.entity_id = entity_id
        self.team = team
        self.decision_count = 0
        self.last_decision: Decision | None = None
        self.last_action: Action | None = None

    @property
    def agent_type(self) -> str:
        return type(self).__name__

    @abstractmethod
    def think(self, observation: Observation) -> Decision:
        """Select a behaviour and record why."""

    @abstractmethod
    def act(self, observation: Observation, decision: Decision) -> Action:
        """Turn the selected behaviour into control demands."""

    def step(self, observation: Observation) -> tuple[Decision, Action]:
        """One full decision cycle."""
        decision = self.think(observation)
        action = self.act(observation, decision)

        self.decision_count += 1
        self.last_decision = decision
        self.last_action = action
        return decision, action

    def update(self, observation: Observation, reward: float = 0.0) -> None:  # noqa: B027
        """Optional hook for learning agents (PHASE 11+).

        Deliberately concrete and empty rather than abstract: a rule-based agent
        has nothing to learn, and forcing every subclass to write an empty
        override would be noise.
        """

    def reset(self) -> None:
        """Clear per-run state. Subclasses extend this."""
        self.decision_count = 0
        self.last_decision = None
        self.last_action = None

    def status(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "entity_id": self.entity_id,
            "team": self.team.value,
            "type": self.agent_type,
            "decision_count": self.decision_count,
            "last_decision": self.last_decision.to_dict() if self.last_decision else None,
        }
