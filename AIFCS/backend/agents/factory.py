"""Build agents from a scenario (PHASE 3).

Keeps scenario parsing and agent construction apart: the scenario declares
*what* each unit should do, this module turns that into agent objects.
"""

from __future__ import annotations

import numpy as np

from agents.base_agent import BaseAgent
from agents.rule_agent import (
    FormationAssignment,
    RouteAssignment,
    RuleAgent,
    RuleAgentConfig,
)
from controllers.autopilot import GuidanceGains
from core.config import Settings
from simulation.scenario import Scenario, ScenarioEntity

AGENT_TYPES = {"rule", "none"}


def build_config(settings: Settings) -> RuleAgentConfig:
    """Rule-agent tuning from ``configs/agents.yaml``."""
    rule = settings.agents.rule_agent
    return RuleAgentConfig(
        waypoint_capture_radius_m=rule.waypoint_capture_radius_m,
        formation_spacing_m=rule.formation_spacing_m,
        formation_station_tolerance_m=rule.formation_station_tolerance_m,
        collision_radius_m=rule.collision_radius_m,
        cruise_speed_mps=rule.cruise_speed_mps,
        decision_interval_s=1.0 / settings.agents.decision_rate_hz,
        gains=GuidanceGains(max_bank_deg=rule.max_bank_deg),
    )


def build_agent(declared: ScenarioEntity, config: RuleAgentConfig, agent_type: str) -> BaseAgent | None:
    """Create the agent for one scenario entity, or None if it flies unpiloted."""
    if agent_type == "none":
        return None

    route = RouteAssignment(
        waypoints=[np.asarray(point, dtype=np.float64) for point in declared.waypoints],
        loop=declared.route_loop,
    )
    formation = (
        FormationAssignment(
            leader_id=declared.formation_leader,
            offset=np.asarray(declared.formation_offset, dtype=np.float64),
        )
        if declared.formation_leader
        else None
    )

    return RuleAgent(
        agent_id=f"AGENT-{declared.id}",
        entity_id=declared.id,
        team=declared.team,
        config=config,
        route=route,
        formation=formation,
    )


def build_agents(scenario: Scenario, settings: Settings) -> list[BaseAgent]:
    """Every agent a scenario calls for."""
    config = build_config(settings)
    default_type = settings.agents.default_type

    agents: list[BaseAgent] = []
    for declared in scenario.entities:
        agent_type = declared.agent or default_type
        agent = build_agent(declared, config, agent_type)
        if agent is not None:
            agents.append(agent)
    return agents
