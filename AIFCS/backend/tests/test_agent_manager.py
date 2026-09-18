"""Agent manager and agent-integration tests (PHASE 3)."""

from __future__ import annotations

import numpy as np
import pytest

from agents.agent_manager import AgentManager, build_observation
from agents.base_agent import Action, BaseAgent, Behaviour, Decision, Observation
from core.event_bus import EventBus, EventType
from core.simulation_engine import SimulationEngine
from core.world_state import EntityState, EntityStatus, Team, WorldState
from simulation.aircraft import ControlInputs


class StubAgent(BaseAgent):
    """Commands a fixed control setting and counts how often it was asked."""

    def __init__(self, entity_id: str = "BLUE-01", controls: ControlInputs | None = None) -> None:
        super().__init__(f"AGENT-{entity_id}", entity_id, Team.BLUE)
        self.controls = controls or ControlInputs(elevator=0.5, throttle=0.8)
        self.seen: list[Observation] = []

    def think(self, observation: Observation) -> Decision:
        self.seen.append(observation)
        return Decision(
            agent_id=self.agent_id,
            entity_id=self.entity_id,
            simulation_time=observation.simulation_time,
            tick=0,
            behaviour=Behaviour.HOLD,
            confidence=1.0,
            reason_codes=[],
            observation_summary=observation.summary(),
        )

    def act(self, observation: Observation, decision: Decision) -> Action:
        return Action(controls=self.controls)


class ExplodingAgent(StubAgent):
    def think(self, observation: Observation) -> Decision:
        raise RuntimeError("agent failure")


def make_world() -> WorldState:
    world = WorldState()
    world.add_entity(
        EntityState(
            id="BLUE-01",
            team=Team.BLUE,
            position=np.array([0.0, 0.0, 6000.0]),
            velocity=np.array([220.0, 0.0, 0.0]),
        )
    )
    world.add_entity(
        EntityState(
            id="RED-01",
            team=Team.RED,
            position=np.array([1000.0, 0.0, 6000.0]),
            velocity=np.array([-220.0, 0.0, 0.0]),
        )
    )
    return world


# ----------------------------------------------------------------- observation


def test_observation_excludes_self_and_marks_friendlies():
    world = make_world()
    agent = StubAgent()
    observation = build_observation(agent, world.get("BLUE-01"), world)

    assert {c.entity_id for c in observation.contacts} == {"RED-01"}
    assert observation.contacts[0].is_friendly is False
    assert observation.contacts[0].distance_m == pytest.approx(1000.0)


def test_observation_skips_inactive_entities():
    world = make_world()
    world.get("RED-01").status = EntityStatus.DISABLED
    observation = build_observation(StubAgent(), world.get("BLUE-01"), world)
    assert observation.contacts == []


def test_observation_does_not_alias_the_truth_state():
    """An agent must not be able to reach back into the world through its view."""
    world = make_world()
    observation = build_observation(StubAgent(), world.get("BLUE-01"), world)

    observation.position[0] = 99999.0
    assert world.get("BLUE-01").position[0] == 0.0


# --------------------------------------------------------------------- timing


def test_agents_decide_at_their_own_rate_not_the_tick_rate():
    manager = AgentManager(EventBus(), tick_rate_hz=60, decision_rate_hz=10.0)
    agent = StubAgent()
    manager.register(agent)
    world = make_world()

    for tick in range(60):  # one simulation second
        manager.update(world, tick)

    assert manager.decision_interval_ticks == 6
    assert agent.decision_count == 10, "10 Hz over one second is ten decisions"


def test_decision_schedule_is_tick_based_and_repeatable():
    def run() -> list[float]:
        manager = AgentManager(EventBus(), tick_rate_hz=60, decision_rate_hz=10.0)
        agent = StubAgent()
        manager.register(agent)
        world = make_world()
        times = []
        for tick in range(120):
            world.simulation_time = tick / 60.0
            if manager.update(world, tick):
                times.append(world.simulation_time)
        return times

    assert run() == run()


def test_a_decision_rate_above_the_tick_rate_falls_back_to_every_tick():
    manager = AgentManager(EventBus(), tick_rate_hz=60, decision_rate_hz=1000.0)
    assert manager.decision_interval_ticks == 1


# ---------------------------------------------------------------- application


def test_the_manager_applies_controls_to_the_entity():
    manager = AgentManager(EventBus(), tick_rate_hz=60, decision_rate_hz=60.0)
    manager.register(StubAgent(controls=ControlInputs(aileron=0.4, throttle=0.9)))
    world = make_world()

    manager.update(world, 0)

    assert world.get("BLUE-01").controls.aileron == pytest.approx(0.4)
    assert world.get("BLUE-01").controls.throttle == pytest.approx(0.9)


def test_the_manager_never_writes_to_the_truth_state():
    """Agents may set controls and nothing else."""
    manager = AgentManager(EventBus(), tick_rate_hz=60, decision_rate_hz=60.0)
    manager.register(StubAgent())
    world = make_world()

    before = world.get("BLUE-01")
    position = before.position.copy()
    velocity = before.velocity.copy()

    manager.update(world, 0)

    assert before.position.tolist() == position.tolist()
    assert before.velocity.tolist() == velocity.tolist()


def test_out_of_range_commands_are_clamped_before_reaching_the_entity():
    manager = AgentManager(EventBus(), tick_rate_hz=60, decision_rate_hz=60.0)
    manager.register(StubAgent(controls=ControlInputs(aileron=5.0, throttle=-3.0)))
    world = make_world()

    manager.update(world, 0)

    assert world.get("BLUE-01").controls.aileron == pytest.approx(1.0)
    assert world.get("BLUE-01").controls.throttle == pytest.approx(0.0)


def test_a_failing_agent_does_not_stop_the_simulation():
    manager = AgentManager(EventBus(), tick_rate_hz=60, decision_rate_hz=60.0)
    manager.register(ExplodingAgent())
    healthy = StubAgent(entity_id="RED-01")
    healthy.team = Team.RED
    manager.register(healthy)
    world = make_world()

    manager.update(world, 0)  # must not raise

    assert healthy.decision_count == 1


def test_agents_are_skipped_when_their_entity_is_inactive():
    manager = AgentManager(EventBus(), tick_rate_hz=60, decision_rate_hz=60.0)
    agent = StubAgent()
    manager.register(agent)
    world = make_world()
    world.get("BLUE-01").status = EntityStatus.DISABLED

    manager.update(world, 0)
    assert agent.decision_count == 0


def test_duplicate_agent_ids_are_rejected():
    manager = AgentManager(EventBus())
    manager.register(StubAgent())
    with pytest.raises(ValueError, match="duplicate"):
        manager.register(StubAgent())


# -------------------------------------------------------------------- events


def test_each_decision_publishes_an_event_with_its_evidence():
    bus = EventBus()
    manager = AgentManager(bus, tick_rate_hz=60, decision_rate_hz=60.0)
    manager.register(StubAgent())

    manager.update(make_world(), 0)

    events = bus.recent(event_type=EventType.AGENT_DECISION)
    assert len(events) == 1
    assert events[0].agent_id == "AGENT-BLUE-01"
    assert events[0].data["behaviour"] == "HOLD"
    assert "action" in events[0].data


def test_the_decision_log_is_bounded():
    manager = AgentManager(EventBus(), tick_rate_hz=60, decision_rate_hz=60.0, decision_log_size=10)
    manager.register(StubAgent())
    world = make_world()

    for tick in range(100):
        manager.update(world, tick)

    assert len(manager.recent_decisions(limit=1000)) == 10


# --------------------------------------------------------------- integration


def test_the_engine_builds_agents_from_the_scenario(settings):
    engine = SimulationEngine(settings=settings)
    engine.load_scenario("demo_alpha")

    assert engine.agents.count == 4
    assert {a.entity_id for a in engine.agents.agents} == {
        "BLUE-01",
        "BLUE-02",
        "RED-01",
        "RED-02",
    }


def test_agents_actually_fly_the_aircraft(settings):
    """The lead should track its route and hold the commanded altitude."""
    engine = SimulationEngine(settings=settings)
    engine.load_scenario("demo_alpha")

    engine.step(60 * 150)  # 150 simulation seconds

    lead = engine.world.get("BLUE-01")
    assert lead.status is EntityStatus.ACTIVE
    assert lead.heading_deg == pytest.approx(90.0, abs=5.0), "should hold its outbound leg"
    assert lead.altitude == pytest.approx(6000.0, abs=50.0), "altitude hold should remove droop"
    assert engine.agents.decision_count > 1000


def test_the_wingman_closes_on_its_station(settings):
    engine = SimulationEngine(settings=settings)
    engine.load_scenario("demo_alpha")

    lead, wing = engine.world.get("BLUE-01"), engine.world.get("BLUE-02")
    initial = float(np.linalg.norm(wing.position - lead.position))

    engine.step(60 * 120)

    wing_agent = next(a for a in engine.agents.agents if a.entity_id == "BLUE-02")
    station_error = wing_agent.last_decision.metrics["station_error_m"]

    assert station_error < 200.0, f"wingman should reach station, error {station_error} m"
    assert initial > 0


def test_formation_partners_do_not_trigger_collision_avoidance(settings):
    """The collision radius must sit well inside the formation spacing."""
    engine = SimulationEngine(settings=settings)
    engine.load_scenario("demo_alpha")

    engine.step(60 * 120)

    behaviours = {a.entity_id: a.last_decision.behaviour for a in engine.agents.agents}
    assert behaviours["BLUE-02"] is Behaviour.FORMATION
    assert behaviours["BLUE-01"] is Behaviour.PATROL


def test_agent_driven_runs_stay_deterministic(settings):
    def run() -> str:
        engine = SimulationEngine(settings=settings)
        engine.load_scenario("demo_alpha")
        engine.step(60 * 60)
        return engine.world.state_hash

    assert run() == run()
