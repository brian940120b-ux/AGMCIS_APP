"""Communication model (PHASE 6).

An abstract datalink between fictional units. It carries simulation messages
between simulated entities and models the ways a link degrades: latency with
jitter, packet loss, reordering, finite bandwidth and blackout windows.

It is a **simulation transport only**. It does no real networking, and it
implements nothing that scans, manipulates, jams or interferes with anything.
The UDP adapter in a later phase is likewise a local process boundary for
research, not a network capability.

Why this matters for the platform: PHASE 5 made an agent's perception imperfect.
A datalink is what lets teammates share what they see — so a unit can know where
its leader is even when its own sensor cannot see it. When the link degrades,
that knowledge degrades with it, which is the point.

Determinism: all randomness comes from a generator seeded with the run seed and
consumed in a fixed order, so the same seed delivers the same messages, loses
the same ones, and reorders them the same way.
"""

from __future__ import annotations

import heapq
import itertools
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import numpy as np


class MessageType(StrEnum):
    """What a message carries."""

    POSITION_REPORT = "POSITION_REPORT"
    TASK_ORDER = "TASK_ORDER"
    STATUS = "STATUS"


@dataclass(frozen=True)
class Message:
    """One datalink message."""

    sender_id: str
    recipient_id: str | None  # None means broadcast to the sender's team
    type: MessageType
    payload: dict[str, Any]
    sent_time: float
    sequence: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "sender_id": self.sender_id,
            "recipient_id": self.recipient_id,
            "type": self.type.value,
            "sent_time": round(self.sent_time, 3),
            "sequence": self.sequence,
        }


@dataclass(frozen=True)
class CommsConfig:
    """Abstract link performance."""

    enabled: bool = True

    # Delivery delay. Jitter is what produces out-of-order arrival, so a
    # receiver cannot assume sequence order.
    latency_base_s: float = 0.15
    latency_jitter_s: float = 0.08

    packet_loss_probability: float = 0.03

    # Bandwidth, as messages accepted per sender per second. Anything beyond it
    # is dropped at the transmitter rather than queued forever.
    max_messages_per_second: float = 20.0

    # How often each unit broadcasts its own position on the datalink.
    report_rate_hz: float = 4.0

    # Windows of simulation time in which nothing gets through, as (start, end).
    blackout_windows: tuple[tuple[float, float], ...] = ()


@dataclass
class CommsStats:
    sent: int = 0
    delivered: int = 0
    lost: int = 0
    dropped_bandwidth: int = 0
    blocked_blackout: int = 0
    reordered: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "sent": self.sent,
            "delivered": self.delivered,
            "lost": self.lost,
            "dropped_bandwidth": self.dropped_bandwidth,
            "blocked_blackout": self.blocked_blackout,
            "reordered": self.reordered,
        }


@dataclass(order=True)
class _InFlight:
    """A message waiting for its delivery time."""

    delivery_time: float
    tiebreak: int
    message: Message = field(compare=False)


class CommunicationModel:
    """Simulated datalink: send, broadcast, receive, with realistic degradation."""

    def __init__(self, config: CommsConfig | None = None, seed: int = 0) -> None:
        self.config = config or CommsConfig()
        self._seed = seed
        self._rng = np.random.default_rng(seed)

        self._in_flight: list[_InFlight] = []
        self._inbox: dict[str, list[Message]] = {}
        self._teams: dict[str, str] = {}
        self._sequence = itertools.count(1)
        self._tiebreak = itertools.count(1)

        # Per-sender send timestamps in the last second, for the bandwidth check.
        self._recent_sends: dict[str, list[float]] = {}
        # Highest sequence number already delivered to each recipient.
        self._last_sequence: dict[str, int] = {}

        self.stats = CommsStats()
        self._blackout_active = False

    # ------------------------------------------------------------- lifecycle

    def reset(self, seed: int | None = None) -> None:
        self._seed = self._seed if seed is None else seed
        self._rng = np.random.default_rng(self._seed)
        self._in_flight.clear()
        self._inbox.clear()
        self._recent_sends.clear()
        self._last_sequence.clear()
        self._sequence = itertools.count(1)
        self._tiebreak = itertools.count(1)
        self.stats = CommsStats()
        self._blackout_active = False

    def register(self, entity_id: str, team: str) -> None:
        """Tell the model which team an entity is on, so broadcast can resolve."""
        self._teams[entity_id] = team

    def clear_participants(self) -> None:
        self._teams.clear()

    # ------------------------------------------------------------- blackouts

    def in_blackout(self, simulation_time: float) -> bool:
        return any(start <= simulation_time <= end for start, end in self.config.blackout_windows)

    # ---------------------------------------------------------------- sending

    def send(
        self,
        sender_id: str,
        recipient_id: str | None,
        message_type: MessageType,
        payload: dict[str, Any],
        simulation_time: float,
    ) -> bool:
        """Queue a message for delivery. Returns False when it never left."""
        if not self.config.enabled:
            return False

        self.stats.sent += 1

        if self.in_blackout(simulation_time):
            self.stats.blocked_blackout += 1
            return False

        if not self._within_bandwidth(sender_id, simulation_time):
            self.stats.dropped_bandwidth += 1
            return False

        # A draw is always taken once the message is admitted, so the random
        # sequence does not depend on which branch earlier messages took.
        if self._rng.random() < self.config.packet_loss_probability:
            self.stats.lost += 1
            return False

        jitter = float(self._rng.normal(0.0, self.config.latency_jitter_s))
        latency = max(0.0, self.config.latency_base_s + jitter)

        message = Message(
            sender_id=sender_id,
            recipient_id=recipient_id,
            type=message_type,
            payload=payload,
            sent_time=simulation_time,
            sequence=next(self._sequence),
        )
        heapq.heappush(
            self._in_flight,
            _InFlight(simulation_time + latency, next(self._tiebreak), message),
        )
        return True

    def broadcast(
        self,
        sender_id: str,
        message_type: MessageType,
        payload: dict[str, Any],
        simulation_time: float,
    ) -> bool:
        """Send to everyone on the sender's team. One transmission, many receivers."""
        return self.send(sender_id, None, message_type, payload, simulation_time)

    def _within_bandwidth(self, sender_id: str, simulation_time: float) -> bool:
        window_start = simulation_time - 1.0
        recent = [t for t in self._recent_sends.get(sender_id, []) if t > window_start]

        if len(recent) >= self.config.max_messages_per_second:
            self._recent_sends[sender_id] = recent
            return False

        recent.append(simulation_time)
        self._recent_sends[sender_id] = recent
        return True

    # -------------------------------------------------------------- delivery

    def update(self, simulation_time: float) -> int:
        """Deliver every message whose latency has elapsed. Returns how many."""
        delivered = 0
        while self._in_flight and self._in_flight[0].delivery_time <= simulation_time:
            message = heapq.heappop(self._in_flight).message
            for recipient in self._recipients_of(message):
                self._inbox.setdefault(recipient, []).append(message)

                # Jitter can reorder messages; a receiver must not assume order.
                previous = self._last_sequence.get(recipient, 0)
                if message.sequence < previous:
                    self.stats.reordered += 1
                else:
                    self._last_sequence[recipient] = message.sequence

            self.stats.delivered += 1
            delivered += 1
        return delivered

    def _recipients_of(self, message: Message) -> list[str]:
        if message.recipient_id is not None:
            return [message.recipient_id]

        team = self._teams.get(message.sender_id)
        return sorted(
            entity_id
            for entity_id, entity_team in self._teams.items()
            if entity_team == team and entity_id != message.sender_id
        )

    def receive(self, entity_id: str) -> list[Message]:
        """Take everything waiting for this entity, in arrival order."""
        return self._inbox.pop(entity_id, [])

    def peek(self, entity_id: str) -> list[Message]:
        """Look without consuming — used by the API for reporting."""
        return list(self._inbox.get(entity_id, []))

    # ------------------------------------------------------------- reporting

    def get_latency(self) -> float:
        """Nominal one-way latency, in seconds."""
        return self.config.latency_base_s

    def get_packet_loss(self) -> float:
        """Configured probability that a transmission is lost."""
        return self.config.packet_loss_probability

    def status(self, simulation_time: float = 0.0) -> dict[str, Any]:
        return {
            "enabled": self.config.enabled,
            "latency_base_s": self.config.latency_base_s,
            "latency_jitter_s": self.config.latency_jitter_s,
            "packet_loss_probability": self.config.packet_loss_probability,
            "max_messages_per_second": self.config.max_messages_per_second,
            "report_rate_hz": self.config.report_rate_hz,
            "blackout_windows": [list(w) for w in self.config.blackout_windows],
            "blackout_active": self.in_blackout(simulation_time),
            "in_flight": len(self._in_flight),
            "participants": len(self._teams),
            "stats": self.stats.to_dict(),
        }
