"""An agent flown by a learned policy (PHASE 11).

The point of this class is that it is *ordinary*. It is a ``BaseAgent`` like
any other, so a policy flies through exactly the same pipeline as a rule agent:

    sensor model → datalink → agent → action validator → envelope protection
    → actuator rate limiting → physics

Nothing is bypassed for training. A policy sees the same noisy, delayed,
sometimes-missing picture a rule agent sees, and its controls are validated and
clamped the same way. A policy that learns to fly in this environment has
learned to fly the simulated aircraft, not to exploit a shortcut.

The controls come from outside: the Gymnasium environment sets them before it
advances the engine, and reads the resulting observation back afterwards.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from agents.base_agent import (
    Action,
    BaseAgent,
    Behaviour,
    Decision,
    Observation,
    ReasonCode,
)
from core.world_state import Team
from simulation.aircraft import ControlInputs


class PolicyAgent(BaseAgent):
    """Flies whatever controls were last handed to it.

    Between calls to :meth:`set_controls` the last commanded controls stand,
    which matches how every other agent behaves between decisions.
    """

    def __init__(self, agent_id: str, entity_id: str, team: Team) -> None:
        super().__init__(agent_id, entity_id, team)
        self.pending = ControlInputs()
        # The environment needs the observation the agent was actually given —
        # not one it rebuilds from truth, which would be a different picture.
        self.last_observation: Observation | None = None
        self.observation_count = 0

    # ----------------------------------------------------------- policy input

    def set_controls(self, controls: ControlInputs) -> None:
        self.pending = controls.clamped()

    # ------------------------------------------------------- agent lifecycle

    def think(self, observation: Observation) -> Decision:
        """Record what the aircraft knew when the policy's controls were applied.

        A policy cannot explain itself the way a rule can, and pretending
        otherwise would be inventing an explanation. What *can* be stated
        truthfully is the measured condition the aircraft was in, so the
        decision carries POLICY_ACTION plus whichever measured codes hold.
        """
        self.last_observation = observation
        self.observation_count += 1

        reasons = [ReasonCode.POLICY_ACTION]
        nearest = observation.nearest_contact
        if nearest is not None and nearest.distance_m < 500.0:
            reasons.append(ReasonCode.COLLISION_RISK)

        metrics: dict[str, Any] = {
            "altitude_m": round(observation.altitude, 1),
            "speed_mps": round(observation.speed, 1),
            "heading_deg": round(observation.heading_deg, 1),
            "contacts": len(observation.contacts),
        }
        if nearest is not None:
            metrics["nearest_contact_m"] = round(nearest.distance_m, 1)

        return Decision(
            agent_id=self.agent_id,
            entity_id=self.entity_id,
            simulation_time=observation.simulation_time,
            tick=0,
            behaviour=Behaviour.POLICY,
            # The policy's own certainty is not observable from here; what is
            # observable is how good the picture was, so that is what is
            # reported rather than a number that looks like confidence in the
            # action.
            confidence=float(np.clip(observation.confidence, 0.0, 1.0)),
            reason_codes=reasons,
            observation_summary=observation.summary(),
            metrics=metrics,
        )

    def act(self, observation: Observation, decision: Decision) -> Action:
        return Action(controls=self.pending)

    def reset(self) -> None:
        super().reset()
        self.pending = ControlInputs()
        self.last_observation = None
        self.observation_count = 0
