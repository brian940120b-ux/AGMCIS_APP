"""Commander, task and team tests (PHASE 14-15).

The first group is the one that matters most: a commander must be structurally
incapable of flying an aircraft. Everything else is behaviour; that is a
guarantee, and a guarantee deserves a test that fails loudly if it erodes.
"""

from __future__ import annotations

import inspect
import re

import pytest

from agents import commander_agent
from agents.commander_agent import CommanderAgent, StandingOrder
from agents.task_manager import TaskManager
from agents.tasks import Task, TaskReason, TaskStatus, TaskType, new_task_id
from agents.team_manager import TeamManager
from core.simulation_engine import SimulationEngine
from core.world_state import EntityStatus, Team


@pytest.fixture
def engine():
    engine = SimulationEngine()
    engine.load_scenario("demo_alpha")
    return engine


@pytest.fixture
def eight():
    engine = SimulationEngine()
    engine.load_scenario("team_eight")
    return engine


# ------------------------------------------------- the commander cannot fly


def test_a_commander_has_no_aircraft():
    """No entity means nothing it could fly."""
    commander = CommanderAgent("CMD-BLUE", Team.BLUE, task_manager=TaskManager(), team_manager=None)
    assert not hasattr(commander, "entity_id")


def test_a_commander_is_not_an_agent():
    """Never registered with the AgentManager, so never asked for an Action."""
    from agents.base_agent import BaseAgent

    assert not issubclass(CommanderAgent, BaseAgent)


def test_commanders_are_absent_from_the_agent_registry(engine):
    assert len(engine.commanders) == 2
    registered = {a.agent_id for a in engine.agents.agents}
    for commander in engine.commanders:
        assert commander.commander_id not in registered


def test_no_commander_appears_in_the_demand_table(engine):
    """The demand table is the only route to a control surface."""
    engine.step(600)
    commander_ids = {c.commander_id for c in engine.commanders}
    assert not (commander_ids & set(engine.agents.demands))


def test_the_commander_module_never_touches_a_control_channel():
    """A source-level check, because this is the guarantee the design rests on.

    Docstrings are stripped first: the module explains at length that it does
    not write controls, and that prose must not be what makes the test pass.
    """
    source = inspect.getsource(commander_agent)
    source = re.sub(r'"""(?:.|\n)*?"""', "", source)
    source = re.sub(r"#.*", "", source)

    for forbidden in ("aileron", "elevator", "rudder", "throttle", "ControlInputs", ".controls", "demands"):
        assert forbidden not in source, f"the commander module references {forbidden!r}"


def test_a_task_carries_no_control_values():
    """Tasks say what to do, never how to move a surface."""
    task = Task(
        task_id=new_task_id(),
        type=TaskType.ESCORT,
        entity_id="BLUE-02",
        issued_by="CMD-BLUE",
        issued_at=0.0,
        parameters={"leader": "BLUE-01", "offset": [-600.0, -600.0, 0.0]},
    )
    encoded = str(task.to_dict())
    for forbidden in ("aileron", "elevator", "rudder", "throttle"):
        assert forbidden not in encoded


# ----------------------------------------------------------------- task ids


def test_task_ids_do_not_collide_in_a_burst():
    """A commander allocates a whole team inside a millisecond.

    A timestamp alone collided, two units shared one record, the second
    overwrote the first, and the register reported a leader escorting itself.
    """
    issued = [new_task_id("B") for _ in range(64)]
    assert len(set(issued)) == 64


# ------------------------------------------------------------- allocation


def test_leaders_patrol_and_wingmen_escort(engine):
    engine.step(60 * 20)
    active = engine.tasks.status()["active"]

    assert active["BLUE-01"]["type"] == TaskType.PATROL.value
    assert active["BLUE-02"]["type"] == TaskType.ESCORT.value
    assert active["BLUE-02"]["parameters"]["leader"] == "BLUE-01"
    assert TaskReason.LEADER_AVAILABLE.value in active["BLUE-02"]["reasons"]


def test_silence_before_the_first_report_is_not_a_lost_leader(engine):
    """The bug this pins: at t=0 no position report has been delivered, so
    nobody has been 'heard from'. Reading that as leader-lost made every
    commander promote every wingman on its first decision, permanently — a run
    began with the formation already dissolved."""
    engine.step(30)  # less than one report interval plus latency
    active = engine.tasks.status()["active"]

    wingman = active.get("BLUE-02")
    assert wingman is not None
    assert wingman["type"] == TaskType.ESCORT.value
    assert TaskReason.LEADER_LOST.value not in wingman["reasons"]


def test_a_wingman_takes_over_when_its_leader_is_lost(engine):
    """Otherwise it holds station on an aircraft that is not coming back."""
    engine.step(60 * 20)
    assert engine.tasks.status()["active"]["BLUE-02"]["type"] == TaskType.ESCORT.value

    engine.world.entities["BLUE-01"].status = EntityStatus.DISABLED
    engine.step(60 * 8)

    promoted = engine.tasks.status()["active"]["BLUE-02"]
    assert promoted["type"] == TaskType.PATROL.value
    assert TaskReason.LEADER_LOST.value in promoted["reasons"]
    assert len(promoted["parameters"]["waypoints"]) == 4

    # The other team is untouched.
    assert engine.tasks.status()["active"]["RED-02"]["type"] == TaskType.ESCORT.value


def test_the_promoted_wingman_really_flies_the_route(engine):
    """The task must reach the agent, not just the register."""
    engine.step(60 * 20)
    engine.world.entities["BLUE-01"].status = EntityStatus.DISABLED
    engine.step(60 * 8)

    wingman = next(a for a in engine.agents.agents if a.entity_id == "BLUE-02")
    assert wingman.formation is None
    assert len(wingman.route.waypoints) == 4


def test_a_commander_can_be_switched_off(settings):
    engine = SimulationEngine(
        settings=settings.model_copy(
            update={"agents": settings.agents.model_copy(update={"commander_enabled": False})}
        )
    )
    engine.load_scenario("demo_alpha")
    engine.step(600)

    assert engine.commanders == []
    assert engine.tasks.issued_count == 0


# ------------------------------------------------------------------- orders


def test_orders_travel_over_the_datalink(engine):
    """A commander whose orders arrived instantly would be in a different
    simulation from the units it commands."""
    engine.step(60 * 20)
    delivered = engine.messages.status()["delivered_by_type"]
    assert delivered.get("TASK_ORDER", 0) > 0
    assert engine.agents.tasks_applied > 0


def test_a_task_order_is_not_eaten_by_the_position_report_drain(engine):
    """The latent bug the CommunicationManager exists to fix: the datalink
    drained the whole inbox and kept only position reports."""
    engine.step(60 * 20)
    delivered = engine.messages.status()["delivered_by_type"]
    assert delivered.get("POSITION_REPORT", 0) > 0
    assert delivered.get("TASK_ORDER", 0) > 0
    assert engine.messages.status()["unrouted"] == 0


def test_an_order_that_never_arrives_stops_counting_as_open():
    """Otherwise a lost order leaves a unit looking tasked forever and the
    commander never reissues."""
    manager = TaskManager()
    task = Task(task_id="T1", type=TaskType.HOLD, entity_id="B1", issued_by="CMD", issued_at=0.0)
    manager.issue(task, 0.0)
    assert manager.has_open_task("B1")

    manager.expire_pending(100.0, timeout_s=10.0)
    assert not manager.has_open_task("B1")
    assert manager.get("T1").status is TaskStatus.LOST


# -------------------------------------------------------------------- teams


def test_the_team_picture_comes_from_the_datalink_not_the_world(engine):
    """Before any report has been delivered a team knows nothing about itself,
    even though every unit is plainly there in the world."""
    manager = TeamManager(engine.datalink)

    start = manager.picture(engine.world, Team.BLUE)
    assert start.active == start.members  # they exist
    assert start.coverage == 0.0  # but nobody has been heard from

    engine.step(60 * 5)
    later = manager.picture(engine.world, Team.BLUE)
    assert later.coverage == 1.0
    assert all(m.report_age_s is not None for m in later.heard)


def test_a_report_carries_its_age(engine):
    engine.step(60 * 5)
    picture = TeamManager(engine.datalink).picture(engine.world, Team.BLUE)
    for member in picture.heard:
        assert 0.0 <= member.report_age_s < 5.0


# -------------------------------------------------------------------- scale


def test_eight_agents_are_all_tasked(eight):
    eight.step(60 * 20)
    active = eight.tasks.status()["active"]

    assert len(eight.agents.agents) == 8
    assert len(eight.commanders) == 2
    assert len(active) == 8
    assert sum(1 for t in active.values() if t["type"] == TaskType.PATROL.value) == 4
    assert sum(1 for t in active.values() if t["type"] == TaskType.ESCORT.value) == 4


def test_eight_agents_stay_deterministic():
    """Coordination must not introduce a source of run-to-run variation."""

    def run() -> str:
        engine = SimulationEngine()
        engine.load_scenario("team_eight")
        engine.step(60 * 15)
        return engine.world.state_hash

    assert run() == run()


def test_a_standing_order_is_the_plan_not_a_position():
    """A commander knows the mission plan; it learns positions from the link."""
    order = StandingOrder(entity_id="BLUE-02", leader_id="BLUE-01")
    assert order.waypoints == []
    assert order.formation_offset == [0.0, 0.0, 0.0]
