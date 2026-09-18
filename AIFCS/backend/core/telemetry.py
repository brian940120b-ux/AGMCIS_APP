"""Telemetry broadcasting (PHASE 7).

Pushes simulation state to connected dashboards over WebSocket, at a rate set
by ``telemetry.broadcast_rate_hz`` — deliberately independent of the 60 Hz
physics tick and of whatever frame rate a browser happens to run at.

Three clocks, three rates, on purpose:

* physics   — fixed 60 Hz, the only one that determines the result
* telemetry — 20 Hz by default, enough for a smooth display
* render    — the browser's business entirely

Each connection carries its own cursor over the event and decision streams, so a
client that connects mid-run receives what happened after it arrived rather than
a replay of the whole run, and two clients never interfere with each other.

A slow or dead connection must never hold up the simulation: sends are bounded
and a connection that fails is dropped.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from core.logging_config import get_logger

log = get_logger("telemetry")


class TelemetrySink(Protocol):
    """Anything that can receive a frame — a WebSocket, or a test double."""

    async def send_json(self, data: dict[str, Any]) -> None: ...


@dataclass
class Subscriber:
    """One connected client and how much of each stream it has seen."""

    sink: TelemetrySink
    last_event_sequence: int = 0
    last_decision_sequence: int = 0
    frames_sent: int = 0
    connected_at: float = field(default_factory=time.time)


class TelemetryBroadcaster:
    """Builds telemetry frames and pushes them to every connected client."""

    def __init__(self, engine: Any, broadcast_rate_hz: float = 20.0) -> None:
        self.engine = engine
        self.broadcast_rate_hz = max(broadcast_rate_hz, 0.1)
        self._subscribers: list[Subscriber] = []
        self._task: asyncio.Task[None] | None = None
        self._sequence = 0
        self.frames_broadcast = 0

    # ------------------------------------------------------------ membership

    async def connect(self, sink: TelemetrySink) -> Subscriber:
        """Register a client and send it one frame immediately.

        The cursors start at whatever the streams are at now, so a client sees
        what happens from here rather than a burst of history.
        """
        subscriber = Subscriber(
            sink=sink,
            last_event_sequence=self.engine.events.sequence,
            last_decision_sequence=self.engine.agents.decision_sequence,
        )
        self._subscribers.append(subscriber)
        await self._send_to(subscriber, self._build_frame(subscriber, kind="snapshot"))
        return subscriber

    def disconnect(self, subscriber: Subscriber) -> None:
        if subscriber in self._subscribers:
            self._subscribers.remove(subscriber)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    # ------------------------------------------------------------- lifecycle

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _run(self) -> None:
        """Broadcast at the configured rate, without drifting."""
        loop = asyncio.get_running_loop()
        interval = 1.0 / self.broadcast_rate_hz
        next_frame = loop.time()
        try:
            while True:
                next_frame += interval
                delay = next_frame - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                else:
                    # Running behind: skip the backlog rather than chase it.
                    await asyncio.sleep(0)
                    next_frame = loop.time()

                if self._subscribers:
                    await self.broadcast()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("telemetry broadcaster failed", extra={"event": "TELEMETRY_ERROR"})
            raise

    # ------------------------------------------------------------ broadcasting

    async def broadcast(self) -> int:
        """Send one frame to every subscriber. Returns how many were reached."""
        self._sequence += 1
        self.frames_broadcast += 1

        sent = 0
        for subscriber in list(self._subscribers):
            frame = self._build_frame(subscriber, kind="telemetry")
            if await self._send_to(subscriber, frame):
                sent += 1
        return sent

    async def _send_to(self, subscriber: Subscriber, frame: dict[str, Any]) -> bool:
        """Deliver one frame, dropping the client if it fails."""
        try:
            await subscriber.sink.send_json(frame)
        except Exception:
            # A dead or slow client is removed; it must not stall the run.
            self.disconnect(subscriber)
            log.info(
                "telemetry client dropped",
                extra={"event": "TELEMETRY_DISCONNECT", "frames_sent": subscriber.frames_sent},
            )
            return False

        subscriber.frames_sent += 1
        return True

    def _build_frame(self, subscriber: Subscriber, kind: str) -> dict[str, Any]:
        """Assemble the frame for one subscriber and advance its cursors."""
        engine = self.engine

        events = engine.events.events_after(subscriber.last_event_sequence)
        decisions = engine.agents.decisions_after(subscriber.last_decision_sequence)
        if events:
            subscriber.last_event_sequence = events[-1].sequence
        if decisions:
            subscriber.last_decision_sequence = decisions[-1].sequence

        world = engine.world
        return {
            "type": kind,
            "sequence": self._sequence,
            "wall_time": time.time(),
            "clock": engine.clock.snapshot(),
            "scenario": engine.scenario.name if engine.scenario else None,
            "state_hash": world.state_hash,
            "entities": [entity.to_dict() for entity in world.entities.values()],
            # Incremental: only what this client has not seen.
            "events": [event.to_dict() for event in events],
            "decisions": [decision.to_dict() for decision in decisions],
            "controller": engine.controller.status(),
            "sensors": engine.sensors.status(),
            "communications": {
                **engine.comms.status(engine.clock.simulation_time),
                "datalink": engine.datalink.status(),
            },
            "agents": {
                "agent_count": engine.agents.count,
                "decision_rate_hz": engine.agents.decision_rate_hz,
                "total_decisions": engine.agents.decision_count,
            },
        }

    def status(self) -> dict[str, Any]:
        return {
            "broadcast_rate_hz": self.broadcast_rate_hz,
            "subscribers": self.subscriber_count,
            "frames_broadcast": self.frames_broadcast,
            "running": self._task is not None and not self._task.done(),
        }
