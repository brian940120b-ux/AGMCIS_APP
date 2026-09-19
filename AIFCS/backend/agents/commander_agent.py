"""A commander that allocates tasks and nothing else (PHASE 15).

The constraint that shapes this whole class: **a commander never touches a
control surface.** It is enforced structurally rather than by discipline —

* it has no ``entity_id``, so there is no aircraft it could fly;
* it is not a ``BaseAgent`` and is never registered with the ``AgentManager``,
  so it is never asked for an ``Action`` and never appears in the demand table;
* its only output is a list of :class:`Task` objects.

There is no code path from a commander to ``entity.controls``. The unit's own
agent decides how to fly a task, and the flight controller remains the only
thing that writes controls. ``test_commander.py`` asserts all of this.

It is also **not omniscient**. A commander sees the picture its team has shared
over the datalink, with the age of each report attached — not the world. A
commander that could see units it had not heard from would coordinate perfectly
in exactly the conditions where real coordination breaks, which would make the
simulation useless for studying the thing it exists to study.

Its orders travel over the same simulated transport as everything else, so they
can be delayed, dropped or blacked out. A unit that never receives an order goes
on doing what it was doing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agents.task_manager import TaskManager
from agents.tasks import Task, TaskReason, TaskType, new_task_id
from core.logging_config import get_logger
from core.world_state import EntityStatus, Team, WorldState
from simulation.communications import CommunicationModel, MessageType

log = get_logger("commander")


@dataclass
class StandingOrder:
    """What the mission plan says a unit is for.

    Taken from the scenario at load: a commander knows the plan it was given.
    It does not know where anything currently *is* except through the datalink.
    """

    entity_id: str
    waypoints: list[list[float]] = field(default_factory=list)
    route_loop: bool = True
    leader_id: str | None = None
    formation_offset: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])


@dataclass
class Allocation:
    """One allocation decision, with the evidence behind it."""

    entity_id: str
    task_type: TaskType
    reasons: list[TaskReason]
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "task_type": self.task_type.value,
            "reasons": [r.value for r in self.reasons],
            "detail": self.detail,
        }


class CommanderAgent:
    """Allocates high-level tasks to one team."""

    def __init__(
        self,
        commander_id: str,
        team: Team,
        *,
        task_manager: TaskManager,
        team_manager: Any,
        comms: CommunicationModel | None = None,
        decision_interval_s: float = 2.0,
        report_stale_s: float = 6.0,
    ) -> None:
        self.commander_id = commander_id
        self.team = team
        self.tasks = task_manager
        self.teams = team_manager
        self.comms = comms
        # Commanders think slowly. Re-allocating faster than orders can arrive
        # and be acted on would just churn.
        self.decision_interval_s = decision_interval_s
        # A report older than this means the unit is effectively unheard.
        self.report_stale_s = report_stale_s

        self.plan: dict[str, StandingOrder] = {}
        self.decision_count = 0
        self.orders_sent = 0
        self.orders_refused = 0
        self.last_allocations: list[Allocation] = []
        self._last_decision_time: float | None = None
        # Until the team has been heard from at least once, "no report" means
        # the run has just begun, not that anybody is lost. See _leader_usable.
        self._heard_anything = False

    # ---------------------------------------------------------------- setup

    def set_plan(self, orders: list[StandingOrder]) -> None:
        self.plan = {order.entity_id: order for order in orders if order.entity_id}

    def reset(self) -> None:
        self.decision_count = 0
        self.orders_sent = 0
        self.orders_refused = 0
        self.last_allocations = []
        self._last_decision_time = None
        self._heard_anything = False

    # --------------------------------------------------------------- the loop

    def update(self, world: WorldState) -> list[Task]:
        """Think, if it is time. Returns the tasks issued this cycle."""
        now = world.simulation_time
        if self._last_decision_time is not None and now - self._last_decision_time < self.decision_interval_s:
            return []
        self._last_decision_time = now
        self.decision_count += 1

        # Orders that never arrived stop counting as open, or this commander
        # would see a tasked unit forever and never reissue.
        self.tasks.expire_pending(now, timeout_s=self.decision_interval_s * 3)

        picture = self.teams.picture(world, self.team)
        if picture.heard:
            self._heard_anything = True

        allocations = self._allocate(picture, world)
        self.last_allocations = allocations

        issued: list[Task] = []
        for allocation in allocations:
            task = self._build_task(allocation, now, picture)
            if task is None:
                continue
            self.tasks.issue(task, now)
            if self._send(task, now):
                issued.append(task)
        return issued

    # ------------------------------------------------------------ allocation

    def _allocate(self, picture: Any, world: WorldState) -> list[Allocation]:
        """Decide what each unit should be doing. Pure judgement, no side effects."""
        allocations: list[Allocation] = []

        for member in picture.active:
            entity_id = member.entity_id
            order = self.plan.get(entity_id)
            current = self.tasks.active_for(entity_id)
            open_task = self.tasks.has_open_task(entity_id)

            # An escort whose leader is gone needs a new job — this is the one
            # reallocation worth making, and the reason it exists is that a
            # wingman left holding station on a dead leader simply orbits.
            if current is not None and current.task.type is TaskType.ESCORT:
                leader_id = str(current.task.parameters.get("leader", ""))
                if not self._leader_usable(leader_id, picture, world):
                    allocations.append(self._promote(entity_id, leader_id, picture))
                    continue

            if open_task:
                continue

            if order is None:
                allocations.append(
                    Allocation(
                        entity_id,
                        TaskType.HOLD,
                        [TaskReason.NO_TASK_ASSIGNED],
                        {"note": "no standing order for this unit"},
                    )
                )
                continue

            if order.leader_id and self._leader_usable(order.leader_id, picture, world):
                allocations.append(
                    Allocation(
                        entity_id, TaskType.ESCORT, [TaskReason.LEADER_AVAILABLE], {"leader": order.leader_id}
                    )
                )
            elif order.leader_id:
                allocations.append(self._promote(entity_id, order.leader_id, picture))
            elif order.waypoints:
                allocations.append(
                    Allocation(
                        entity_id,
                        TaskType.PATROL,
                        [TaskReason.ROUTE_AVAILABLE],
                        {"waypoints": len(order.waypoints)},
                    )
                )
            else:
                allocations.append(
                    Allocation(
                        entity_id,
                        TaskType.HOLD,
                        [TaskReason.NO_TASK_ASSIGNED],
                        {"note": "no route and no leader"},
                    )
                )

        return allocations

    def _promote(self, entity_id: str, leader_id: str, picture: Any) -> Allocation:
        """A wingman whose leader is gone takes over the leader's route.

        Without this the wingman holds station on an aircraft that is not
        coming back and simply orbits for the rest of the run.
        """
        leader_order = self.plan.get(leader_id)
        if leader_order is not None and leader_order.waypoints:
            return Allocation(
                entity_id,
                TaskType.PATROL,
                [TaskReason.LEADER_LOST, TaskReason.REBALANCING_COVERAGE],
                {"took_over_route_from": leader_id, "waypoints": len(leader_order.waypoints)},
            )
        return Allocation(
            entity_id,
            TaskType.HOLD,
            [TaskReason.LEADER_LOST],
            {"leader": leader_id, "note": "leader gone and no route to take over"},
        )

    def _leader_usable(self, leader_id: str, picture: Any, world: WorldState) -> bool:
        """Can this unit still be escorted?

        Two separate questions. Whether the aircraft is still flying comes from
        the world — existence is not secret. Whether the team can actually
        follow it comes from the datalink, because a leader nobody has heard
        from in six seconds cannot be held station on.

        The distinction that matters, and that this got wrong first time: at
        the start of a run nobody has been heard from yet, because no position
        report has been delivered. Treating that as "leader lost" made every
        commander promote every wingman on its first decision, permanently —
        a run began with the formation already dissolved. Silence before the
        first report is the run beginning, not a loss.
        """
        entity = world.get(leader_id)
        if entity is None or entity.status is not EntityStatus.ACTIVE:
            return False

        member = picture.member(leader_id)
        if member is None or not member.heard_from:
            # Trust the plan until the team has actually been heard from once.
            return not self._heard_anything
        return (member.report_age_s or 0.0) <= self.report_stale_s

    # ---------------------------------------------------------------- orders

    def _build_task(self, allocation: Allocation, now: float, picture: Any) -> Task | None:
        order = self.plan.get(allocation.entity_id)
        parameters: dict[str, Any] = {}

        if allocation.task_type is TaskType.ESCORT:
            leader_id = str(allocation.detail.get("leader", ""))
            leader_order = self.plan.get(allocation.entity_id)
            parameters = {
                "leader": leader_id,
                "offset": list(leader_order.formation_offset) if leader_order else [0.0, 0.0, 0.0],
            }
        elif allocation.task_type is TaskType.PATROL:
            source = allocation.detail.get("took_over_route_from")
            route_order = self.plan.get(str(source)) if source else order
            if route_order is None or not route_order.waypoints:
                return None
            parameters = {
                "waypoints": [list(p) for p in route_order.waypoints],
                "loop": route_order.route_loop,
            }
        elif allocation.task_type is TaskType.HOLD:
            member = picture.member(allocation.entity_id)
            parameters = {
                # Where the team last heard it was — the commander has nothing
                # better, and saying so is more useful than pretending to know.
                "at": member.position.tolist() if member and member.position is not None else None
            }

        return Task(
            task_id=new_task_id(f"{self.team.value[0]}"),
            type=allocation.task_type,
            entity_id=allocation.entity_id,
            issued_by=self.commander_id,
            issued_at=now,
            parameters=parameters,
            reasons=tuple(allocation.reasons),
        )

    def _send(self, task: Task, now: float) -> bool:
        """Put the order on the datalink. It may not get there."""
        if self.comms is None:
            # No transport: the task manager still has the record, and the
            # engine applies it directly. Used by tests and headless rollouts.
            self.tasks.activate(task.task_id, now)
            self.orders_sent += 1
            return True

        accepted = self.comms.send(
            sender_id=self.commander_id,
            recipient_id=task.entity_id,
            message_type=MessageType.TASK_ORDER,
            payload=task.to_dict(),
            simulation_time=now,
        )
        if accepted:
            self.orders_sent += 1
        else:
            self.orders_refused += 1
            self.tasks.mark_lost(task.task_id, now, "the link refused the order")
        return accepted

    # ---------------------------------------------------------------- status

    def status(self) -> dict[str, Any]:
        return {
            "commander_id": self.commander_id,
            "team": self.team.value,
            "decision_count": self.decision_count,
            "decision_interval_s": self.decision_interval_s,
            "orders_sent": self.orders_sent,
            "orders_refused": self.orders_refused,
            "plan_size": len(self.plan),
            "last_allocations": [a.to_dict() for a in self.last_allocations],
            "writes_controls": False,
        }
