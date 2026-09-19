"""High-level tasks (PHASE 15).

A task says *what* a unit should be doing — patrol this circuit, hold station
on that leader, transit to this point. It never says how to move a control
surface. That separation is the whole design: a commander allocates tasks, a
unit's own agent decides how to fly them, and the flight controller remains the
only thing that writes controls.

Tasks are fictional and abstract, like everything else here: routes and
stations. There is no engagement, targeting or weapon task, and adding
one would mean giving the platform a capability it does not have.

A task's life:

    PENDING  issued, still travelling over the datalink
    ACTIVE   received and taken up by the unit
    COMPLETE the unit finished it
    FAILED   the unit could not do it (its leader is gone, say)
    SUPERSEDED a newer order for the same unit replaced it
    LOST     never arrived; the link dropped it
"""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class TaskType(StrEnum):
    """What a unit is being asked to do."""

    PATROL = "PATROL"  # fly a circuit of waypoints, repeating
    TRANSIT = "TRANSIT"  # fly to one point and hold there
    ESCORT = "ESCORT"  # hold station on another unit
    HOLD = "HOLD"  # maintain present position and altitude


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    SUPERSEDED = "SUPERSEDED"
    LOST = "LOST"


class TaskReason(StrEnum):
    """Why the commander allocated this task. Each maps to a measured condition."""

    NO_TASK_ASSIGNED = "NO_TASK_ASSIGNED"
    ROUTE_AVAILABLE = "ROUTE_AVAILABLE"
    LEADER_AVAILABLE = "LEADER_AVAILABLE"
    LEADER_LOST = "LEADER_LOST"
    TASK_COMPLETE = "TASK_COMPLETE"
    UNIT_UNRESPONSIVE = "UNIT_UNRESPONSIVE"
    REBALANCING_COVERAGE = "REBALANCING_COVERAGE"


@dataclass(frozen=True)
class Task:
    """One order. Immutable — a change of plan is a new task, not an edit."""

    task_id: str
    type: TaskType
    entity_id: str
    issued_by: str
    issued_at: float
    # What the task needs, by type: waypoints for PATROL, a point for TRANSIT,
    # a leader and offset for ESCORT. Every type here is one a unit can
    # actually carry out — a task nothing executes would be decoration.
    parameters: dict[str, Any] = field(default_factory=dict)
    priority: int = 5
    reasons: tuple[TaskReason, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "type": self.type.value,
            "entity_id": self.entity_id,
            "issued_by": self.issued_by,
            "issued_at": round(self.issued_at, 3),
            "parameters": self.parameters,
            "priority": self.priority,
            "reasons": [r.value for r in self.reasons],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Task:
        """Rebuild a task from a datalink payload."""
        return cls(
            task_id=str(payload["task_id"]),
            type=TaskType(payload["type"]),
            entity_id=str(payload["entity_id"]),
            issued_by=str(payload.get("issued_by", "")),
            issued_at=float(payload.get("issued_at", 0.0)),
            parameters=dict(payload.get("parameters", {})),
            priority=int(payload.get("priority", 5)),
            reasons=tuple(TaskReason(r) for r in payload.get("reasons", [])),
        )


@dataclass
class TaskRecord:
    """A task plus what has happened to it since."""

    task: Task
    status: TaskStatus = TaskStatus.PENDING
    issued_at: float = 0.0
    activated_at: float | None = None
    finished_at: float | None = None
    note: str = ""

    @property
    def is_open(self) -> bool:
        return self.status in (TaskStatus.PENDING, TaskStatus.ACTIVE)

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.task.to_dict(),
            "status": self.status.value,
            "activated_at": self.activated_at,
            "finished_at": self.finished_at,
            "note": self.note,
        }


# Tasks are issued in bursts — a commander allocates its whole team in one
# pass, well inside a millisecond — so a timestamp alone is not an identity.
# It collided, and two units ended up sharing one task record: the second
# overwrote the first, and the register then reported a leader escorting
# itself. The counter is what makes an id an id; the timestamp only keeps them
# sortable and readable.
_counter = itertools.count(1)


def new_task_id(prefix: str = "T") -> str:
    """Short, sortable, and actually unique within a run."""
    return f"{prefix}-{int(time.time() * 1000) % 100_000_000:08d}-{next(_counter):04d}"


def reset_task_ids() -> None:
    """Restart the counter. Used by tests that assert on exact ids."""
    global _counter
    _counter = itertools.count(1)
