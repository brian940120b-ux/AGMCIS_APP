"""Event bus tests (PHASE 1)."""

from __future__ import annotations

from core.event_bus import Event, EventBus, EventType


def test_subscriber_receives_only_its_event_type():
    bus = EventBus()
    started: list[Event] = []
    paused: list[Event] = []
    bus.subscribe(EventType.SIMULATION_STARTED, started.append)
    bus.subscribe(EventType.SIMULATION_PAUSED, paused.append)

    bus.emit(EventType.SIMULATION_STARTED, message="go")

    assert len(started) == 1
    assert paused == []


def test_subscribe_all_receives_everything():
    bus = EventBus()
    seen: list[Event] = []
    bus.subscribe_all(seen.append)

    bus.emit(EventType.SIMULATION_STARTED)
    bus.emit(EventType.SCORE_CHANGED)

    assert [e.type for e in seen] == [EventType.SIMULATION_STARTED, EventType.SCORE_CHANGED]


def test_unsubscribe_stops_delivery():
    bus = EventBus()
    seen: list[Event] = []
    unsubscribe = bus.subscribe(EventType.SIMULATION_STARTED, seen.append)

    bus.emit(EventType.SIMULATION_STARTED)
    unsubscribe()
    bus.emit(EventType.SIMULATION_STARTED)

    assert len(seen) == 1


def test_failing_subscriber_does_not_break_the_others():
    """A broken consumer must never take the simulation down."""
    bus = EventBus()
    delivered: list[Event] = []

    def broken(_: Event) -> None:
        raise RuntimeError("subscriber failure")

    bus.subscribe(EventType.SIMULATION_STARTED, broken)
    bus.subscribe(EventType.SIMULATION_STARTED, delivered.append)

    bus.emit(EventType.SIMULATION_STARTED)
    assert len(delivered) == 1


def test_tick_events_are_not_retained_in_history():
    """Ticks fire 60x a second and would evict everything worth reading."""
    bus = EventBus()
    bus.emit(EventType.SIMULATION_STARTED)
    for tick in range(500):
        bus.emit(EventType.SIMULATION_TICK, tick=tick)

    history = bus.recent(limit=100)
    assert [e.type for e in history] == [EventType.SIMULATION_STARTED]
    assert bus.published_count == 501


def test_history_is_bounded():
    bus = EventBus(history_size=10)
    for _ in range(50):
        bus.emit(EventType.STATE_CHANGED)
    assert len(bus.recent(limit=100)) == 10


def test_recent_filters_by_type_and_returns_newest_last():
    bus = EventBus()
    bus.emit(EventType.SIMULATION_STARTED, message="first")
    bus.emit(EventType.SCORE_CHANGED, message="second")
    bus.emit(EventType.SIMULATION_PAUSED, message="third")

    assert bus.recent()[-1].message == "third"
    assert [e.message for e in bus.recent(event_type=EventType.SCORE_CHANGED)] == ["second"]


def test_clear_history_keeps_the_published_counter():
    bus = EventBus()
    bus.emit(EventType.SIMULATION_STARTED)
    bus.clear_history()
    assert bus.recent() == []
    assert bus.published_count == 1


def test_event_serialises_for_the_api():
    payload = Event(type=EventType.AGENT_DECISION, agent_id="BLUE-01", message="REGROUP").to_dict()
    assert payload["type"] == "AGENT_DECISION"
    assert payload["agent_id"] == "BLUE-01"
    assert {"simulation_time", "tick", "entity_id", "data", "wall_time"} <= set(payload)
