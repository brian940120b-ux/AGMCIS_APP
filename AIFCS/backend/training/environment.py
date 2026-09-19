"""AIFCSCombatEnv — the Gymnasium environment (PHASE 11).

Wraps the real simulation engine. Not a reduced model of it, not a faster
approximation: the same fixed-timestep 6DOF physics, the same sensor model with
its noise and dropout, the same datalink, and the same safety layer between the
policy's output and the control surfaces.

That costs speed and buys the only thing that matters — a policy trained here
has been trained against the system it will actually fly.

One unit is controlled by the policy. Every other unit in the scenario is flown
by its ordinary rule agent, so the policy learns in traffic rather than alone.

Despite the name inherited from the project plan, nothing in this environment
models weapons, engagement or targeting. The task is flight and navigation.
"""

from __future__ import annotations

from typing import Any

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from agents.base_agent import Observation
from agents.factory import build_agent, build_config
from core.config import Settings, get_settings
from core.logging_config import get_logger
from core.world_state import EntityStatus, Team
from simulation.aircraft import ControlInputs
from simulation.scenario import Scenario, ScenarioEntity
from training.observation_encoder import EncoderConfig, Goal, encode
from training.policy_agent import PolicyAgent
from training.reward import RewardConfig, RewardEngine

log = get_logger("training.env")

# Four normalised control channels, all in [-1, 1]. Throttle is rescaled to
# [0, 1] on the way out: a policy should not have to learn that half its
# throttle range is meaningless.
ACTION_CHANNELS = ("aileron", "elevator", "rudder", "throttle")


class AIFCSCombatEnv(gym.Env[np.ndarray, np.ndarray]):
    """A single policy-controlled unit inside a full AIFCS scenario."""

    metadata = {"render_modes": []}

    def __init__(
        self,
        *,
        scenario_name: str | None = None,
        entity_id: str | None = None,
        settings: Settings | None = None,
        max_episode_seconds: float = 120.0,
        encoder: EncoderConfig | None = None,
        reward_config: RewardConfig | None = None,
        seed: int | None = None,
    ) -> None:
        super().__init__()
        # Imported here rather than at module scope: core.simulation_engine
        # pulls in the whole simulation, and training/ should not make that a
        # hard import for anyone who only wanted the encoder.
        from core.simulation_engine import SimulationEngine

        self.settings = settings or get_settings()
        self.scenario_name = scenario_name or self.settings.scenarios.default_scenario
        self.requested_entity_id = entity_id
        self.base_seed = seed if seed is not None else self.settings.simulation.seed

        self.engine = SimulationEngine(settings=self.settings)
        self.encoder_config = encoder or EncoderConfig(
            max_altitude_m=self.settings.world.bounds.altitude_max,
            max_range_m=self.settings.sensors.max_range_m,
            track_memory_s=self.settings.sensors.track_memory_s,
        )

        # The policy acts once per agent decision, not once per physics tick:
        # it must not be able to react faster than a rule agent can.
        tick_rate = self.settings.simulation.tick_rate_hz
        self.ticks_per_step = max(1, round(tick_rate / self.settings.agents.decision_rate_hz))
        self.step_seconds = self.ticks_per_step / tick_rate
        self.max_episode_steps = max(1, int(max_episode_seconds / self.step_seconds))

        self.reward_engine = RewardEngine(
            weights=self.settings.training.reward_weights,
            config=reward_config or self._default_reward_config(),
        )

        self.observation_space = spaces.Box(
            low=-1.0, high=1.0, shape=(self.encoder_config.size,), dtype=np.float32
        )
        self.action_space = spaces.Box(low=-1.0, high=1.0, shape=(4,), dtype=np.float32)

        self.policy_agent: PolicyAgent | None = None
        self.scenario: Scenario | None = None
        self.declared: ScenarioEntity | None = None
        self._route: list[np.ndarray] = []
        self._route_index = 0
        self._leader_id: str | None = None
        self._leader_offset = np.zeros(3)
        self._steps = 0
        self._episode_reward = 0.0
        self._episode = 0
        self._last_breakdown: dict[str, Any] | None = None

    def _default_reward_config(self) -> RewardConfig:
        """Thresholds calibrated to this environment's own step.

        ``progress_scale_m`` is the one that matters. It has to be the distance
        a step can actually cover — at cruise that is speed x step_seconds,
        about 22 m at 220 m/s and 10 Hz. Left at a round 200 m the navigation
        term could never exceed 0.11 while survival paid 1.0 every step, and a
        policy would have almost no reason to go anywhere. Deriving it keeps
        the two terms comparable if the decision rate ever changes.
        """
        cruise = self.settings.agents.rule_agent.cruise_speed_mps
        return RewardConfig(
            progress_scale_m=max(1.0, cruise * self.step_seconds),
            collision_radius_m=self.settings.agents.rule_agent.collision_radius_m,
            formation_tolerance_m=self.settings.agents.rule_agent.formation_station_tolerance_m * 2,
            min_altitude_m=self.settings.safety.min_altitude_m,
            max_altitude_m=self.settings.safety.max_altitude_m,
            track_age_tolerance_s=self.settings.sensors.track_memory_s,
        )

    # ------------------------------------------------------------- lifecycle

    def reset(
        self, *, seed: int | None = None, options: dict[str, Any] | None = None
    ) -> tuple[np.ndarray, dict[str, Any]]:
        super().reset(seed=seed)
        # Each episode gets its own seed, derived from the base one, so a run
        # is reproducible as a whole while episodes are not all identical.
        episode_seed = self.base_seed + self._episode if seed is None else seed
        self._episode += 1

        scenario = self.engine.load_scenario(self.scenario_name, seed=episode_seed)
        self.scenario = scenario

        entity_id = self.requested_entity_id or self._default_entity(scenario)
        self.declared = next(e for e in scenario.entities if e.id == entity_id)

        # Replace this unit's rule agent with a policy agent, and leave every
        # other unit under its own rule agent: the policy learns in traffic.
        self.engine.agents.clear()
        rule_config = build_config(self.settings)
        for declared in scenario.entities:
            if declared.id == entity_id:
                self.policy_agent = PolicyAgent(
                    agent_id=f"POLICY-{declared.id}",
                    entity_id=declared.id,
                    team=Team(declared.team),
                )
                self.engine.agents.register(self.policy_agent)
            else:
                agent = build_agent(
                    declared, rule_config, declared.agent or self.settings.agents.default_type
                )
                if agent is not None:
                    self.engine.agents.register(agent)

        self._route = [np.asarray(p, dtype=np.float64) for p in self.declared.waypoints]
        self._route_index = 0
        self._leader_id = self.declared.formation_leader
        self._leader_offset = np.asarray(self.declared.formation_offset, dtype=np.float64)

        self._steps = 0
        self._episode_reward = 0.0
        self.reward_engine.reset()
        self._last_breakdown = None

        # One decision interval so the sensor model has produced a picture;
        # an all-zero first observation would teach the policy nothing.
        self.engine.step(self.ticks_per_step)

        return self._observation(), self._info(terminated_reason=None)

    def step(self, action: np.ndarray) -> tuple[np.ndarray, float, bool, bool, dict[str, Any]]:
        if self.policy_agent is None:
            raise RuntimeError("step() called before reset()")

        self.policy_agent.set_controls(self._to_controls(action))
        self.engine.step(self.ticks_per_step)
        self._steps += 1

        entity = self.engine.world.get(self.policy_agent.entity_id)
        if entity is None:
            # The unit left the world entirely; end the episode rather than
            # scoring a state that no longer exists.
            return (
                np.zeros(self.encoder_config.size, dtype=np.float32),
                -50.0,
                True,
                False,
                self._info(terminated_reason="removed"),
            )

        terminated_reason = self._termination_reason(entity)
        breakdown = self.reward_engine.compute(
            entity=entity,
            observation=self._current_observation(),
            goal_position=self._goal_position(),
            leader_offset_error_m=self._station_error(),
            team_size=self._team_size(),
            terminated_reason=terminated_reason,
        )
        self._last_breakdown = breakdown.to_dict()
        self._episode_reward += breakdown.total

        self._advance_route(entity)

        terminated = terminated_reason is not None
        truncated = not terminated and self._steps >= self.max_episode_steps
        return (
            self._observation(),
            float(breakdown.total),
            terminated,
            truncated,
            self._info(terminated_reason=terminated_reason),
        )

    def close(self) -> None:
        self.engine.agents.clear()

    # --------------------------------------------------------------- helpers

    def _default_entity(self, scenario: Scenario) -> str:
        """Train the first agent-flown unit unless told otherwise."""
        for declared in scenario.entities:
            if (declared.agent or self.settings.agents.default_type) != "none":
                return declared.id
        raise ValueError(f"scenario {scenario.name!r} has no agent-flown unit to train")

    @staticmethod
    def _to_controls(action: np.ndarray) -> ControlInputs:
        """Map the policy's [-1, 1] output onto control demands.

        Throttle is the exception: it runs 0..1 physically, so the channel is
        rescaled rather than letting half the policy's range mean "off".
        """
        values = np.clip(np.asarray(action, dtype=np.float64).reshape(4), -1.0, 1.0)
        # A non-finite action would otherwise become a non-finite demand; the
        # safety layer would reject it, but silently, every step.
        values = np.nan_to_num(values, nan=0.0, posinf=1.0, neginf=-1.0)
        return ControlInputs(
            aileron=float(values[0]),
            elevator=float(values[1]),
            rudder=float(values[2]),
            throttle=float((values[3] + 1.0) / 2.0),
        )

    def _current_observation(self) -> Observation:
        """The picture the agent was actually given this step."""
        assert self.policy_agent is not None
        observation = self.policy_agent.last_observation
        if observation is not None:
            return observation

        # Before the first decision the agent has no observation yet. An empty
        # one is honest: it says the aircraft knows nothing, which is true.
        entity = self.engine.world.get(self.policy_agent.entity_id)
        return Observation(
            agent_id=self.policy_agent.agent_id,
            entity_id=self.policy_agent.entity_id,
            team=self.policy_agent.team,
            simulation_time=self.engine.world.simulation_time,
            position=entity.position.copy() if entity else np.zeros(3),
            velocity=entity.velocity.copy() if entity else np.zeros(3),
            orientation=entity.orientation.copy() if entity else np.zeros(3),
            angular_velocity=entity.angular_velocity.copy() if entity else np.zeros(3),
            altitude=entity.altitude if entity else 0.0,
            speed=entity.speed if entity else 0.0,
            heading_deg=entity.heading_deg if entity else 0.0,
            contacts=[],
            confidence=0.0,
        )

    def _observation(self) -> np.ndarray:
        goal = self._goal_position()
        return encode(
            self._current_observation(),
            Goal(position=goal) if goal is not None else None,
            self.encoder_config,
        )

    def _goal_position(self) -> np.ndarray | None:
        """Where the policy is being asked to go this step.

        A unit with a route heads for its active waypoint. A wingman's goal is
        its station on the leader, which moves — so it is recomputed each step
        from the leader's current position rather than fixed at reset.
        """
        if self._route:
            return self._route[self._route_index % len(self._route)]
        if self._leader_id:
            leader = self.engine.world.get(self._leader_id)
            if leader is not None:
                return leader.position + self._leader_offset
        return None

    def _advance_route(self, entity: Any) -> None:
        if not self._route:
            return
        target = self._route[self._route_index % len(self._route)]
        reached = float(np.linalg.norm(target - entity.position))
        if reached <= self.reward_engine.config.goal_capture_radius_m:
            self._route_index += 1
            # The next leg is a new goal, so the progress term must not see the
            # jump in distance as a giant step backwards.
            self.reward_engine._previous_goal_distance = None

    def _station_error(self) -> float | None:
        if not self._leader_id or self.policy_agent is None:
            return None
        leader = self.engine.world.get(self._leader_id)
        entity = self.engine.world.get(self.policy_agent.entity_id)
        if leader is None or entity is None:
            return None
        station = leader.position + self._leader_offset
        return float(np.linalg.norm(station - entity.position))

    def _team_size(self) -> int:
        if self.policy_agent is None:
            return 0
        return sum(1 for e in self.engine.world.entities.values() if e.team is self.policy_agent.team)

    def _termination_reason(self, entity: Any) -> str | None:
        if entity.status is EntityStatus.OUT_OF_BOUNDS:
            return "out_of_bounds"
        if entity.status is EntityStatus.DISABLED:
            return "disabled"
        if entity.status is not EntityStatus.ACTIVE:
            return entity.status.value.lower()
        if entity.altitude <= self.settings.safety.min_altitude_m:
            return "ground_impact"
        return None

    def _info(self, *, terminated_reason: str | None) -> dict[str, Any]:
        """Everything needed to explain the step, for logging and debugging."""
        entity = self.engine.world.get(self.policy_agent.entity_id) if self.policy_agent else None
        return {
            "scenario": self.scenario_name,
            "entity_id": self.policy_agent.entity_id if self.policy_agent else None,
            "step": self._steps,
            "simulation_time": round(self.engine.world.simulation_time, 3),
            "episode_reward": round(self._episode_reward, 4),
            "goals_reached": self.reward_engine.goals_reached,
            "waypoint_index": self._route_index if self._route else None,
            "altitude_m": round(entity.altitude, 1) if entity else None,
            "speed_mps": round(entity.speed, 1) if entity else None,
            "station_error_m": self._station_error(),
            "terminated_reason": terminated_reason,
            "reward": self._last_breakdown,
        }

    # ------------------------------------------------------------------ spec

    def spec_summary(self) -> dict[str, Any]:
        """A description of the environment, for the API and the model card."""
        from training.observation_encoder import LAYOUT_VERSION

        return {
            "id": "AIFCSCombatEnv-v0",
            "scenario": self.scenario_name,
            "entity_id": self.requested_entity_id,
            "observation_size": self.encoder_config.size,
            "observation_layout_version": LAYOUT_VERSION,
            "action_channels": list(ACTION_CHANNELS),
            "decision_rate_hz": self.settings.agents.decision_rate_hz,
            "ticks_per_step": self.ticks_per_step,
            "step_seconds": round(self.step_seconds, 4),
            "max_episode_steps": self.max_episode_steps,
            "reward_weights": self.settings.training.reward_weights.model_dump(),
            "notice": (
                "Flight and navigation only. This environment models no weapon, "
                "engagement or targeting capability, and rewards none."
            ),
        }
