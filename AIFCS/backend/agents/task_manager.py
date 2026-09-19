"""Tracking who was told to do what (PHASE 15).

The commander issues orders; this remembers them. It is deliberately separate
from the commander: what *was ordered* is a fact about the run, while *what to
order next* is a judgement, and the record should outlive any particular
commander's opinion.

A task is only ACTIVE once the unit has taken it up. Between issue and
acknowledgement it is PENDING, because the order is still crossing a datalink
that can delay or lose it — and an order that never arrived must not look like
one being carried out.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from agents.tasks import Task, TaskRecord, TaskStatus
from core.logging_config import get_logger

log = get_logger("task_manager")


class TaskManager:
    """The record of every task issued in a run."""

    def __init__(self, history_limit: int = 500) -> None:
        self._records: dict[str, TaskRecord] = {}
        self._by_entity: dict[str, list[str]] = defaultdict(list)
        self._order: list[str] = []
        self._history_limit = history_limit

    def reset(self) -> None:
        self._records.clear()
        self._by_entity.clear()
        self._order.clear()

    # --------------------------------------------------------------- issuing

    def issue(self, task: Task, simulation_time: float) -> TaskRecord:
        """Record a newly issued task, superseding any open one for that unit.

        A unit has one job at a time. Issuing a second without closing the
        first would leave two open records and no way to say which the unit is
        actually flying.
        """
        for record in self.open_for(task.entity_id):
            record.status = TaskStatus.SUPERSEDED
            record.finished_at = simulation_time
            record.note = f"replaced by {task.task_id}"

        record = TaskRecord(task=task, status=TaskStatus.PENDING, issued_at=simulation_time)
        self._records[task.task_id] = record
        self._by_entity[task.entity_id].append(task.task_id)
        self._order.append(task.task_id)
        self._prune()

        log.info(
            "task issued",
            extra={
                "event": "TASK_ISSUED",
                "task_id": task.task_id,
                "type": task.type.value,
                "entity_id": task.entity_id,
                "issued_by": task.issued_by,
                "reasons": [r.value for r in task.reasons],
            },
        )
        return record

    # -------------------------------------------------------------- lifecycle

    def activate(self, task_id: str, simulation_time: float) -> TaskRecord | None:
        """The unit has received the order and taken it up."""
        record = self._records.get(task_id)
        if record is None or record.status is not TaskStatus.PENDING:
            return None
        record.status = TaskStatus.ACTIVE
        record.activated_at = simulation_time
        return record

    def complete(self, task_id: str, simulation_time: float, note: str = "") -> TaskRecord | None:
        return self._finish(task_id, TaskStatus.COMPLETE, simulation_time, note)

    def fail(self, task_id: str, simulation_time: float, note: str = "") -> TaskRecord | None:
        return self._finish(task_id, TaskStatus.FAILED, simulation_time, note)

    def mark_lost(self, task_id: str, simulation_time: float, note: str = "") -> TaskRecord | None:
        """The order never arrived. Distinct from failing to carry one out."""
        record = self._records.get(task_id)
        if record is None or record.status is not TaskStatus.PENDING:
            return None
        return self._finish(task_id, TaskStatus.LOST, simulation_time, note or "order never arrived")

    def _finish(
        self, task_id: str, status: TaskStatus, simulation_time: float, note: str
    ) -> TaskRecord | None:
        record = self._records.get(task_id)
        if record is None or not record.is_open:
            return None
        record.status = status
        record.finished_at = simulation_time
        record.note = note
        log.info(
            "task finished",
            extra={
                "event": "TASK_FINISHED",
                "task_id": task_id,
                "status": status.value,
                "entity_id": record.task.entity_id,
            },
        )
        return record

    def expire_pending(self, simulation_time: float, timeout_s: float) -> list[TaskRecord]:
        """Give up on orders that have been in flight too long.

        Without this a lost order leaves a unit looking tasked forever, and the
        commander never reissues because it can see an open record.
        """
        expired = []
        for record in self._records.values():
            if record.status is TaskStatus.PENDING and simulation_time - record.issued_at > timeout_s:
                self.mark_lost(record.task.task_id, simulation_time)
                expired.append(record)
        return expired

    # --------------------------------------------------------------- queries

    def get(self, task_id: str) -> TaskRecord | None:
        return self._records.get(task_id)

    def open_for(self, entity_id: str) -> list[TaskRecord]:
        return [
            self._records[task_id]
            for task_id in self._by_entity.get(entity_id, [])
            if task_id in self._records and self._records[task_id].is_open
        ]

    def active_for(self, entity_id: str) -> TaskRecord | None:
        for record in reversed(self.open_for(entity_id)):
            if record.status is TaskStatus.ACTIVE:
                return record
        return None

    def has_open_task(self, entity_id: str) -> bool:
        return bool(self.open_for(entity_id))

    def recent(self, limit: int = 50) -> list[TaskRecord]:
        return [self._records[t] for t in self._order[-limit:] if t in self._records]

    @property
    def issued_count(self) -> int:
        return len(self._order)

    def _prune(self) -> None:
        """Keep the history bounded; a long run must not grow without limit."""
        while len(self._order) > self._history_limit:
            oldest = self._order.pop(0)
            record = self._records.pop(oldest, None)
            if record is not None:
                ids = self._by_entity.get(record.task.entity_id, [])
                if oldest in ids:
                    ids.remove(oldest)

    def status(self) -> dict[str, Any]:
        counts: dict[str, int] = defaultdict(int)
        for record in self._records.values():
            counts[record.status.value] += 1
        return {
            "issued": self.issued_count,
            "tracked": len(self._records),
            "by_status": dict(counts),
            "active": {
                entity_id: active.task.to_dict()
                for entity_id in sorted(self._by_entity)
                if (active := self.active_for(entity_id)) is not None
            },
        }
