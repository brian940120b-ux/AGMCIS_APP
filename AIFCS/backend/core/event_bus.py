"""System event bus (PHASE 1).

Every meaningful thing that happens in AIFCS is published here. The replay
recorder, scoring engine, WebSocket telemetry and logging all consume the same
stream, so no subsystem has to reach into another.

A subscriber that raises must never take the simulation down: exceptions are
caught and logged, and delivery continues to the remaining subscribers.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from core.logging_config import get_logger

log = get_logger("event_bus")


class EventType(StrEnum):
    # Simulation lifecycle
    SIMULATION_STARTED = "SIMULATION_STARTED"
    SIMULATION_PAUSED = "SIMULATION_PAUSED"
    SIMULATION_RESUMED = "SIMULATION_RESUMED"
    SIMULATION_RESET = "SIMULATION_RESET"
    SIMULATION_ENDED = "SIMULATION_ENDED"
    SIMULATION_SPEED_CHANGED = "SIMULATION_SPEED_CHANGED"
    SIMULATION_TICK = "SIMULATION_TICK"

    # World
    STATE_CHANGED = "STATE_CHANGED"
    ENTITY_OUT_OF_BOUNDS = "ENTITY_OUT_OF_BOUNDS"
    COLLISION = "COLLISION"

    # Agents (PHASE 3+)
    AGENT_DECISION = "AGENT_DECISION"
    AGENT_ACTION = "AGENT_ACTION"
    ACTION_REJECTED = "ACTION_REJECTED"

    # Communication (PHASE 6+)
    COMMUNICATION_EVENT = "COMMUNICATION_EVENT"

    # Coordination (PHASE 15)
    TASK_ASSIGNED = "TASK_ASSIGNED"
    TASK_COMPLETED = "TASK_COMPLETED"

    # Scoring / training (PHASE 9+ / PHASE 11+)
    SCORE_CHANGED = "SCORE_CHANGED"
    REWARD_UPDATED = "REWARD_UPDATED"
    TRAINING_STARTED = "TRAINING_STARTED"
    TRAINING_COMPLETED = "TRAINING_COMPLETED"
    REPLAY_STARTED = "REPLAY_STARTED"
    REPLAY_ENDED = "REPLAY_ENDED"


@dataclass(frozen=True)
class Event:
    """One immutable occurrence on the bus.

    ``sequence`` is assigned by the bus on publish and increases monotonically
    for the lifetime of a run, so a telemetry client can ask for "everything
    after N" without relying on timestamps.
    """

    type: EventType
    simulation_time: float = 0.0
    tick: int = 0
    entity_id: str | None = None
    agent_id: str | None = None
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    wall_time: float = field(default_factory=time.time)
    sequence: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "type": self.type.value,
            "simulation_time": self.simulation_time,
            "tick": self.tick,
            "entity_id": self.entity_id,
            "agent_id": self.agent_id,
            "message": self.message,
            "data": self.data,
            "wall_time": self.wall_time,
        }


Subscriber = Callable[[Event], None]


class EventBus:
    """Synchronous publish/subscribe hub with a bounded recent-event history."""

    def __init__(self, history_size: int = 500) -> None:
        self._sequence = 0
        self._subscribers: dict[EventType, list[Subscriber]] = {}
        self._global_subscribers: list[Subscriber] = []
        self._history: deque[Event] = deque(maxlen=history_size)
        self._published_count = 0

    # ----------------------------------------------------------- subscribing

    def subscribe(self, event_type: EventType, handler: Subscriber) -> Callable[[], None]:
        """Subscribe to one event type. Returns a function that unsubscribes."""
        self._subscribers.setdefault(event_type, []).append(handler)

        def unsubscribe() -> None:
            handlers = self._subscribers.get(event_type, [])
            if handler in handlers:
                handlers.remove(handler)

        return unsubscribe

    def subscribe_all(self, handler: Subscriber) -> Callable[[], None]:
        """Subscribe to every event — used by replay, telemetry and logging."""
        self._global_subscribers.append(handler)

        def unsubscribe() -> None:
            if handler in self._global_subscribers:
                self._global_subscribers.remove(handler)

        return unsubscribe

    # ------------------------------------------------------------ publishing

    def publish(self, event: Event) -> Event:
        """Deliver an event to all matching subscribers.

        Returns the event with its sequence assigned. SIMULATION_TICK fires at
        the tick rate, so it is kept out of the history buffer to avoid evicting
        events anyone actually reads.
        """
        self._published_count += 1
        self._sequence += 1
        event = replace(event, sequence=self._sequence)
        if event.type is not EventType.SIMULATION_TICK:
            self._history.append(event)

        for handler in (*self._subscribers.get(event.type, ()), *self._global_subscribers):
            try:
                handler(event)
            except Exception:
                # A broken subscriber must not stop the simulation.
                log.exception(
                    "event subscriber raised",
                    extra={"event": "SUBSCRIBER_ERROR", "event_type": event.type.value},
                )

        return event

    def emit(self, event_type: EventType, **kwargs: Any) -> Event:
        """Convenience: build an Event and publish it in one call."""
        return self.publish(Event(type=event_type, **kwargs))

    # -------------------------------------------------------------- history

    def recent(self, limit: int = 50, event_type: EventType | None = None) -> list[Event]:
        """Most recent events, newest last."""
        events = list(self._history)
        if event_type is not None:
            events = [e for e in events if e.type is event_type]
        return events[-limit:]

    def events_after(self, sequence: int, limit: int = 200) -> list[Event]:
        """Events newer than a sequence the caller has already seen.

        Bounded by the history buffer, so a client that falls far behind gets
        the most recent window rather than everything it missed.
        """
        return [e for e in self._history if e.sequence > sequence][-limit:]

    def clear_history(self) -> None:
        self._history.clear()

    @property
    def sequence(self) -> int:
        """Highest sequence assigned so far."""
        return self._sequence

    @property
    def published_count(self) -> int:
        return self._published_count

    @property
    def subscriber_count(self) -> int:
        return sum(len(v) for v in self._subscribers.values()) + len(self._global_subscribers)
