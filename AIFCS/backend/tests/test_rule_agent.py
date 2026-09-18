"""Rule agent behaviour tests (PHASE 3).

The point of these is that a decision is always traceable: the behaviour chosen
and the reason codes emitted must follow from the observation, never from
anything the agent invented.
"""

from __future__ import annotations

import numpy as np
import pytest

from agents.base_agent import Behaviour, ContactView, Observation, ReasonCode
from agents.guidance import GuidanceGains, GuidanceState, bearing_deg, compute_controls, heading_error_deg
from agents.rule_agent import (
    FormationAssignment,
    RouteAssignment,
    RuleAgent,
    RuleAgentConfig,
)
from core.world_state import Team


def make_observation(
    position=(0.0, 0.0, 6000.0),
    velocity=(220.0, 0.0, 0.0),
    contacts: list[ContactView] | None = None,
    **overrides,
) -> Observation:
    position_array = np.array(position, dtype=float)
    velocity_array = np.array(velocity, dtype=float)
    speed = float(np.linalg.norm(velocity_array))
    heading = float(np.degrees(np.arctan2(velocity_array[0], velocity_array[1])) % 360.0)

    defaults = {
        "agent_id": "AGENT-TEST",
        "entity_id": "TEST-01",
        "team": Team.BLUE,
        "simulation_time": 1.0,
        "position": position_array,
        "velocity": velocity_array,
        "orientation": np.zeros(3),
        "angular_velocity": np.zeros(3),
        "altitude": float(position_array[2]),
        "speed": speed,
        "heading_deg": heading,
        "contacts": contacts or [],
    }
    return Observation(**{**defaults, **overrides})


def make_contact(entity_id="RED-01", offset=(1000.0, 0.0, 0.0), friendly=False, velocity=(0.0, 0.0, 0.0)):
    relative = np.array(offset, dtype=float)
    return ContactView(
        entity_id=entity_id,
        team=Team.BLUE if friendly else Team.RED,
        relative_position=relative,
        relative_velocity=np.array(velocity, dtype=float),
        distance_m=float(np.linalg.norm(relative)),
        is_friendly=friendly,
    )


def make_agent(**kwargs) -> RuleAgent:
    return RuleAgent("AGENT-TEST", "TEST-01", Team.BLUE, **kwargs)


# --------------------------------------------------------------- pure maths


@pytest.mark.parametrize(
    ("target", "current", "expected"),
    [(90.0, 90.0, 0.0), (10.0, 350.0, 20.0), (350.0, 10.0, -20.0)],
)
def test_heading_error_takes_the_short_way_round(target, current, expected):
    assert heading_error_deg(target, current) == pytest.approx(expected)


def test_heading_error_at_exactly_opposite_is_a_half_turn():
    """A course reversal has no shorter side; either sign is correct, and the
    result must be deterministic so replays match."""
    assert abs(heading_error_deg(180.0, 0.0)) == pytest.approx(180.0)
    assert heading_error_deg(180.0, 0.0) == heading_error_deg(180.0, 0.0)


@pytest.mark.parametrize(
    ("offset", "expected"),
    [
        ((0.0, 1000.0, 0.0), 0.0),
        ((1000.0, 0.0, 0.0), 90.0),
        ((0.0, -1000.0, 0.0), 180.0),
        ((-1000.0, 0.0, 0.0), 270.0),
    ],
)
def test_bearing_uses_compass_convention(offset, expected):
    assert bearing_deg(np.zeros(3), np.array(offset)) == pytest.approx(expected)


# -------------------------------------------------------- behaviour choice


def test_no_assignment_holds():
    decision = make_agent().think(make_observation())
    assert decision.behaviour is Behaviour.HOLD
    assert ReasonCode.NO_ROUTE_ASSIGNED in decision.reason_codes


def test_single_waypoint_navigates():
    agent = make_agent(route=RouteAssignment(waypoints=[np.array([20000.0, 0.0, 6000.0])], loop=False))
    decision = agent.think(make_observation())

    assert decision.behaviour is Behaviour.NAVIGATE
    assert ReasonCode.WAYPOINT_ACTIVE in decision.reason_codes
    assert decision.metrics["waypoint_distance_m"] == pytest.approx(20000.0, abs=1.0)


def test_multiple_waypoints_patrol():
    agent = make_agent(
        route=RouteAssignment(waypoints=[np.array([20000.0, 0.0, 6000.0]), np.array([0.0, 20000.0, 6000.0])])
    )
    assert agent.think(make_observation()).behaviour is Behaviour.PATROL


def test_reaching_a_waypoint_advances_the_route():
    config = RuleAgentConfig(waypoint_capture_radius_m=800.0)
    agent = make_agent(
        config=config,
        route=RouteAssignment(waypoints=[np.array([100.0, 0.0, 6000.0]), np.array([0.0, 20000.0, 6000.0])]),
    )
    decision = agent.think(make_observation())

    assert ReasonCode.WAYPOINT_REACHED in decision.reason_codes
    assert agent.route.index == 1


def test_a_non_looping_route_completes_and_holds():
    agent = make_agent(route=RouteAssignment(waypoints=[np.array([100.0, 0.0, 6000.0])], loop=False))
    agent.think(make_observation())  # captures the only waypoint
    decision = agent.think(make_observation())

    assert decision.behaviour is Behaviour.HOLD
    assert ReasonCode.ROUTE_COMPLETE in decision.reason_codes


def test_a_looping_route_wraps_around():
    agent = make_agent(
        route=RouteAssignment(waypoints=[np.array([100.0, 0.0, 6000.0]), np.array([0.0, 20000.0, 6000.0])])
    )
    agent.route.index = 1
    agent.think(make_observation(position=(0.0, 20000.0, 6000.0)))
    assert agent.route.index == 0


def test_collision_risk_overrides_the_route():
    config = RuleAgentConfig(collision_radius_m=200.0)
    agent = make_agent(
        config=config, route=RouteAssignment(waypoints=[np.array([20000.0, 0.0, 6000.0])], loop=False)
    )
    decision = agent.think(make_observation(contacts=[make_contact(offset=(120.0, 0.0, 0.0))]))

    assert decision.behaviour is Behaviour.AVOID
    assert ReasonCode.COLLISION_RISK in decision.reason_codes
    assert decision.metrics["conflict_with"] == "RED-01"


def test_a_contact_outside_the_collision_radius_is_ignored():
    config = RuleAgentConfig(collision_radius_m=200.0)
    agent = make_agent(
        config=config, route=RouteAssignment(waypoints=[np.array([20000.0, 0.0, 6000.0])], loop=False)
    )
    decision = agent.think(make_observation(contacts=[make_contact(offset=(5000.0, 0.0, 0.0))]))

    assert decision.behaviour is Behaviour.NAVIGATE
    assert ReasonCode.COLLISION_RISK not in decision.reason_codes


def test_avoid_steers_away_from_the_conflict():
    agent = make_agent(config=RuleAgentConfig(collision_radius_m=200.0))
    observation = make_observation(contacts=[make_contact(offset=(0.0, 150.0, 0.0))])  # due north

    decision = agent.think(observation)
    action = agent.act(observation, decision)

    assert action.target_heading_deg == pytest.approx(180.0)  # turn south, away
    assert action.target_altitude_m > observation.altitude  # and climb over it


def test_formation_reports_station_error():
    agent = make_agent(
        formation=FormationAssignment(leader_id="BLUE-01", offset=np.array([-600.0, -600.0, 0.0]))
    )
    leader = make_contact("BLUE-01", offset=(600.0, 600.0, 0.0), friendly=True)
    decision = agent.think(make_observation(contacts=[leader]))

    assert decision.behaviour is Behaviour.FORMATION
    assert ReasonCode.FORMATION_ASSIGNED in decision.reason_codes
    assert decision.metrics["station_error_m"] == pytest.approx(1697.0, abs=1.0)


def test_formation_in_station_is_reported():
    agent = make_agent(
        config=RuleAgentConfig(formation_station_tolerance_m=150.0),
        formation=FormationAssignment(leader_id="BLUE-01", offset=np.array([-600.0, -600.0, 0.0])),
    )
    leader = make_contact("BLUE-01", offset=(-600.0, -600.0, 0.0), friendly=True)
    decision = agent.think(make_observation(contacts=[leader]))

    assert ReasonCode.FORMATION_IN_STATION in decision.reason_codes
    assert ReasonCode.FORMATION_SEPARATION_HIGH not in decision.reason_codes


def test_missing_leader_is_reported_and_does_not_crash():
    agent = make_agent(formation=FormationAssignment(leader_id="GHOST", offset=np.zeros(3)))
    decision = agent.think(make_observation())

    assert ReasonCode.LEADER_UNAVAILABLE in decision.reason_codes
    assert decision.behaviour is Behaviour.HOLD


# --------------------------------------------------------------- explainability


def test_reason_codes_reflect_measured_tracking_errors():
    agent = make_agent(route=RouteAssignment(waypoints=[np.array([0.0, 40000.0, 9000.0])], loop=False))
    # Flying east at 6000 m, told to go north and climb to 9000 m.
    decision = agent.think(make_observation())

    assert ReasonCode.ALTITUDE_BELOW_TARGET in decision.reason_codes
    assert ReasonCode.HEADING_ERROR_LARGE in decision.reason_codes
    assert decision.metrics["altitude_error_m"] == pytest.approx(3000.0, abs=1.0)


def test_altitude_above_target_is_distinguished():
    agent = make_agent(route=RouteAssignment(waypoints=[np.array([20000.0, 0.0, 3000.0])], loop=False))
    decision = agent.think(make_observation())
    assert ReasonCode.ALTITUDE_ABOVE_TARGET in decision.reason_codes


def test_every_decision_carries_its_evidence():
    agent = make_agent(route=RouteAssignment(waypoints=[np.array([20000.0, 0.0, 6000.0])], loop=False))
    payload = agent.think(make_observation()).to_dict()

    assert payload["reason_codes"], "a decision must say why"
    assert 0.0 <= payload["confidence"] <= 1.0
    assert {"altitude_m", "speed_mps", "heading_deg", "contacts"} <= set(payload["observation"])


def test_confidence_falls_as_the_wingman_drifts_off_station():
    def confidence_at(offset_m: float) -> float:
        agent = make_agent(formation=FormationAssignment(leader_id="BLUE-01", offset=np.zeros(3)))
        leader = make_contact("BLUE-01", offset=(offset_m, 0.0, 0.0), friendly=True)
        return agent.think(make_observation(contacts=[leader])).confidence

    assert confidence_at(50.0) > confidence_at(800.0)


# ------------------------------------------------------------------ guidance


def test_guidance_commands_right_bank_for_a_right_turn():
    controls = compute_controls(
        orientation=np.zeros(3),
        angular_velocity=np.zeros(3),
        altitude_m=6000.0,
        speed_mps=220.0,
        heading_deg=90.0,
        target_heading_deg=120.0,  # turn right
        target_altitude_m=6000.0,
        target_speed_mps=220.0,
        gains=GuidanceGains(),
    )
    assert controls.aileron > 0


def test_guidance_commands_nose_up_to_climb():
    controls = compute_controls(
        orientation=np.zeros(3),
        angular_velocity=np.zeros(3),
        altitude_m=6000.0,
        speed_mps=220.0,
        heading_deg=90.0,
        target_heading_deg=90.0,
        target_altitude_m=8000.0,
        target_speed_mps=220.0,
        gains=GuidanceGains(),
    )
    assert controls.elevator > 0


def test_guidance_opens_the_throttle_when_slow():
    def throttle_at(speed: float) -> float:
        return compute_controls(
            orientation=np.zeros(3),
            angular_velocity=np.zeros(3),
            altitude_m=6000.0,
            speed_mps=speed,
            heading_deg=90.0,
            target_heading_deg=90.0,
            target_altitude_m=6000.0,
            target_speed_mps=220.0,
            gains=GuidanceGains(),
        ).throttle

    assert throttle_at(150.0) > throttle_at(280.0)


def test_guidance_output_is_always_within_limits():
    controls = compute_controls(
        orientation=np.array([2.0, 1.0, 0.0]),
        angular_velocity=np.array([50.0, 50.0, 50.0]),
        altitude_m=0.0,
        speed_mps=5.0,
        heading_deg=0.0,
        target_heading_deg=180.0,
        target_altitude_m=20000.0,
        target_speed_mps=600.0,
        gains=GuidanceGains(),
    )
    assert -1.0 <= controls.aileron <= 1.0
    assert -1.0 <= controls.elevator <= 1.0
    assert 0.0 <= controls.throttle <= 1.0


def test_altitude_integrator_is_bounded():
    gains = GuidanceGains()
    state = GuidanceState()
    for _ in range(10_000):
        compute_controls(
            orientation=np.zeros(3),
            angular_velocity=np.zeros(3),
            altitude_m=0.0,
            speed_mps=220.0,
            heading_deg=90.0,
            target_heading_deg=90.0,
            target_altitude_m=10000.0,
            target_speed_mps=220.0,
            gains=gains,
            state=state,
            dt=0.1,
        )
    assert abs(state.altitude_integral) <= gains.altitude_integral_limit


def test_reset_clears_route_and_integrator():
    agent = make_agent(
        route=RouteAssignment(waypoints=[np.array([100.0, 0.0, 6000.0]), np.array([0.0, 100.0, 6000.0])])
    )
    observation = make_observation()
    agent.step(observation)
    agent.step(observation)

    agent.reset()

    assert agent.route.index == 0
    assert agent.decision_count == 0
    assert agent.last_decision is None
