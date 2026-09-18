"""Agent manager (PHASE 3).

Owns the agents, runs them on their own clock and applies what they decide.

Agents think at ``agents.decision_rate_hz`` (10 Hz by default), not at the
physics rate (60 Hz). The interval is counted in **ticks**, never in wall time,
so the decision schedule is part of the deterministic run: replaying a seed
reproduces the same decisions at the same ticks.

Between decisions the last commanded controls stay applied, which is what a real
control system does — it does not revert to neutral while waiting for the next
command.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

import numpy as np

from agents.base_agent import Action, BaseAgent, ContactView, Decision, Observation
from core.event_bus import EventBus, EventType
from core.logging_config import get_logger
from core.world_state import EntityState, EntityStatus, WorldState
from simulation.aircraft import ControlInputs

if TYPE_CHECKING:
    from simulation.datalink import DatalinkService
    from simulation.sensors import SensorModel

log = get_logger("agent_manager")


def build_observation(agent: BaseAgent, entity: EntityState, world: WorldState) -> Observation:
    """Undegraded view of the world, straight from truth.

    This is the PHASE 3 behaviour, kept for tests and for studies that want a
    perfect-information baseline. A run with sensing enabled goes through
    ``SensorModel.observe`` instead — that is the only difference, which is what
    the Observation type was designed to make possible.
    """
    contacts: list[ContactView] = []
    for other in world.entities.values():
        if other.id == entity.id or other.status is not EntityStatus.ACTIVE:
            continue
        relative_position = other.position - entity.position
        contacts.append(
            ContactView(
                entity_id=other.id,
                team=other.team,
                relative_position=relative_position,
                relative_velocity=other.velocity - entity.velocity,
                distance_m=float(np.linalg.norm(relative_position)),
                is_friendly=other.team is entity.team,
            )
        )

    return Observation(
        agent_id=agent.agent_id,
        entity_id=entity.id,
        team=entity.team,
        simulation_time=world.simulation_time,
        position=entity.position.copy(),
        velocity=entity.velocity.copy(),
        orientation=entity.orientation.copy(),
        angular_velocity=entity.angular_velocity.copy(),
        altitude=entity.altitude,
        speed=entity.speed,
        heading_deg=entity.heading_deg,
        contacts=contacts,
        confidence=1.0,
    )


class AgentManager:
    """Registry and scheduler for the agents in a run."""

    def __init__(
        self,
        event_bus: EventBus,
        tick_rate_hz: int = 60,
        decision_rate_hz: float = 10.0,
        decision_log_size: int = 500,
        sensor_model: SensorModel | None = None,
        datalink: DatalinkService | None = None,
    ) -> None:
        self.events = event_bus
        # When present, agents perceive the world through it instead of reading
        # truth. Nothing else about the agent path changes.
        self.sensor_model = sensor_model
        # When present, teammates' shared position reports fill gaps the
        # aircraft's own sensor cannot see.
        self.datalink = datalink
        self.tick_rate_hz = tick_rate_hz
        self.decision_rate_hz = decision_rate_hz
        # At least one tick: a decision rate above the tick rate cannot be met.
        self.decision_interval_ticks = max(1, round(tick_rate_hz / max(decision_rate_hz, 1e-6)))

        self._agents: dict[str, BaseAgent] = {}
        # Standing control demand per entity. The flight controller consumes
        # this every physics tick; the agent refreshes it far more slowly.
        self._demands: dict[str, ControlInputs] = {}
        self._decisions: list[Decision] = []
        self._decision_sequence = 0
        self._decision_log_size = decision_log_size
        self._last_decision_tick: dict[str, int] = {}

    # ------------------------------------------------------------- registry

    def register(self, agent: BaseAgent) -> None:
        if agent.agent_id in self._agents:
            raise ValueError(f"duplicate agent id: {agent.agent_id}")
        self._agents[agent.agent_id] = agent

    def clear(self) -> None:
        self._agents.clear()
        self._decisions.clear()
        self._demands.clear()
        self._last_decision_tick.clear()

    def reset(self) -> None:
        for agent in self._agents.values():
            agent.reset()
        self._decisions.clear()
        self._demands.clear()
        self._last_decision_tick.clear()
        self._decision_sequence = 0

    @property
    def decision_sequence(self) -> int:
        """Highest decision sequence assigned so far."""
        return self._decision_sequence

    @property
    def demands(self) -> dict[str, ControlInputs]:
        """The latest command each agent asked for, before the safety layer."""
        return self._demands

    @property
    def agents(self) -> list[BaseAgent]:
        return list(self._agents.values())

    @property
    def count(self) -> int:
        return len(self._agents)

    # ------------------------------------------------------------------ step

    def update(self, world: WorldState, tick: int) -> int:
        """Run every agent that is due this tick. Returns how many decided."""
        decided = 0
        for agent in self._agents.values():
            entity = world.get(agent.entity_id)
            if entity is None or entity.status is not EntityStatus.ACTIVE:
                continue

            last = self._last_decision_tick.get(agent.agent_id)
            if last is not None and tick - last < self.decision_interval_ticks:
                continue

            self._run_agent(agent, entity, world, tick)
            self._last_decision_tick[agent.agent_id] = tick
            decided += 1
        return decided

    def _observe(self, agent: BaseAgent, entity: EntityState, world: WorldState) -> Observation:
        """What the agent is allowed to know this cycle.

        Sensing comes first, then the datalink fills what the aircraft's own
        sensor could not see. A measured contact always wins over a relayed
        one for the same unit.
        """
        if self.sensor_model is not None:
            observation = self.sensor_model.observe(agent.agent_id, entity, world)
        else:
            observation = build_observation(agent, entity, world)

        if self.datalink is not None:
            self.datalink.collect(entity.id)
            observation = self.datalink.merge(observation, entity)

        return observation

    def _run_agent(self, agent: BaseAgent, entity: EntityState, world: WorldState, tick: int) -> None:
        observation = self._observe(agent, entity, world)

        try:
            decision, action = agent.step(observation)
        except Exception:
            # One misbehaving agent must not take the simulation down. The unit
            # keeps its last controls and the failure is recorded.
            log.exception(
                "agent raised during decision",
                extra={
                    "event": "AGENT_ERROR",
                    "agent_id": agent.agent_id,
                    "entity_id": agent.entity_id,
                    "tick": tick,
                },
            )
            return

        self._apply(entity, action)
        self._record(decision, action, tick)

    def _apply(self, entity: EntityState, action: Action) -> None:
        """Record what the agent asked for.

        The manager deliberately does **not** write to the entity. Since PHASE 4
        the flight controller is the only thing that touches entity controls, so
        every command goes through validation, envelope protection and rate
        limiting on its way to the physics.
        """
        self._demands[entity.id] = action.controls

    def _record(self, decision: Decision, action: Action, tick: int) -> None:
        self._decision_sequence += 1
        decision = replace(decision, sequence=self._decision_sequence)
        self._decisions.append(decision)
        if len(self._decisions) > self._decision_log_size:
            del self._decisions[: len(self._decisions) - self._decision_log_size]

        self.events.emit(
            EventType.AGENT_DECISION,
            simulation_time=decision.simulation_time,
            tick=tick,
            entity_id=decision.entity_id,
            agent_id=decision.agent_id,
            message=f"{decision.entity_id}: {decision.behaviour.value}",
            data={
                "behaviour": decision.behaviour.value,
                "confidence": round(decision.confidence, 3),
                "reason_codes": [code.value for code in decision.reason_codes],
                "metrics": decision.metrics,
                "action": action.to_dict(),
            },
        )

    # ------------------------------------------------------------- reporting

    def recent_decisions(self, limit: int = 50, agent_id: str | None = None) -> list[Decision]:
        decisions = self._decisions
        if agent_id is not None:
            decisions = [d for d in decisions if d.agent_id == agent_id]
        return decisions[-limit:]

    def decisions_after(self, sequence: int, limit: int = 200) -> list[Decision]:
        """Decisions newer than a sequence the caller has already seen.

        Bounded by the decision log, so a client that falls far behind receives
        the most recent window rather than everything it missed.
        """
        return [d for d in self._decisions if d.sequence > sequence][-limit:]

    @property
    def decision_count(self) -> int:
        return sum(agent.decision_count for agent in self._agents.values())

    def status(self) -> dict[str, Any]:
        return {
            "agent_count": self.count,
            "decision_rate_hz": self.decision_rate_hz,
            "decision_interval_ticks": self.decision_interval_ticks,
            "total_decisions": self.decision_count,
            "agents": [agent.status() for agent in self._agents.values()],
        }
