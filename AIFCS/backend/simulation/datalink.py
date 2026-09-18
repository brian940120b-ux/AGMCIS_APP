"""Datalink track sharing (PHASE 6).

Turns the generic communication transport into something an agent can use: each
unit broadcasts where it believes it is, and its teammates fold those reports
into their own picture.

This is what makes a limited sensor survivable. In PHASE 5 a wingman with
forward-only coverage lost its leader the moment it overshot. With a datalink it
still knows where the leader is — until the link degrades, and then it does not.
That coupling is the point.

A datalink track is **not** better than truth: it carries the sender's own
imperfect estimate of itself, it is as stale as the link is slow, and it
disappears when messages stop arriving.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from agents.base_agent import ContactView, Observation
from core.world_state import EntityState, Team
from simulation.communications import CommunicationModel, MessageType


@dataclass
class DatalinkTrack:
    """The latest position report received about one teammate."""

    entity_id: str
    team: Team
    position: np.ndarray
    velocity: np.ndarray
    reported_time: float

    def estimate(self, now: float) -> np.ndarray:
        """Dead-reckoned from the report, since the report is already stale."""
        return self.position + self.velocity * max(0.0, now - self.reported_time)


class DatalinkService:
    """Broadcasts position reports and merges what arrives into observations."""

    def __init__(self, comms: CommunicationModel, tick_rate_hz: int = 60) -> None:
        self.comms = comms
        self.tick_rate_hz = tick_rate_hz
        self._tracks: dict[str, dict[str, DatalinkTrack]] = {}
        self._last_report_tick: dict[str, int] = {}

    def reset(self) -> None:
        self._tracks.clear()
        self._last_report_tick.clear()

    @property
    def report_interval_ticks(self) -> int:
        rate = max(self.comms.config.report_rate_hz, 1e-6)
        return max(1, round(self.tick_rate_hz / rate))

    # ------------------------------------------------------------ broadcasting

    def broadcast_reports(self, world, tick: int) -> int:
        """Have each unit report its own state, at the configured rate.

        The reported state is the unit's own estimate of itself, not truth: a
        teammate cannot learn more about you than you know about yourself.
        """
        if not self.comms.config.enabled:
            return 0

        sent = 0
        interval = self.report_interval_ticks
        for entity_id in sorted(world.entities):
            entity = world.entities[entity_id]
            last = self._last_report_tick.get(entity_id)
            if last is not None and tick - last < interval:
                continue

            accepted = self.comms.broadcast(
                sender_id=entity_id,
                message_type=MessageType.POSITION_REPORT,
                payload={
                    "position": entity.position.tolist(),
                    "velocity": entity.velocity.tolist(),
                    "team": entity.team.value,
                },
                simulation_time=world.simulation_time,
            )
            self._last_report_tick[entity_id] = tick
            sent += int(accepted)
        return sent

    # -------------------------------------------------------------- receiving

    def collect(self, entity_id: str) -> None:
        """Drain this unit's inbox into its datalink picture."""
        tracks = self._tracks.setdefault(entity_id, {})
        for message in self.comms.receive(entity_id):
            if message.type is not MessageType.POSITION_REPORT:
                continue
            payload = message.payload
            tracks[message.sender_id] = DatalinkTrack(
                entity_id=message.sender_id,
                team=Team(payload["team"]),
                position=np.asarray(payload["position"], dtype=np.float64),
                velocity=np.asarray(payload["velocity"], dtype=np.float64),
                reported_time=message.sent_time,
            )

    def merge(self, observation: Observation, observer: EntityState) -> Observation:
        """Fold datalink tracks into an observation.

        A sensor contact wins over a datalink report for the same unit: it was
        measured by this aircraft, now, rather than relayed. Datalink fills the
        gaps the sensor cannot see.
        """
        tracks = self._tracks.get(observer.id, {})
        if not tracks:
            return observation

        known = {contact.entity_id for contact in observation.contacts}
        now = observation.simulation_time
        contacts = list(observation.contacts)

        for entity_id in sorted(tracks):
            if entity_id in known or entity_id == observer.id:
                continue

            track = tracks[entity_id]
            age = max(0.0, now - track.reported_time)
            # A report that has not been refreshed for a while is worthless; the
            # unit has had time to manoeuvre away from it.
            if age > self.comms.config.latency_base_s + 3.0:
                continue

            estimated = track.estimate(now)
            relative = estimated - observation.position
            contacts.append(
                ContactView(
                    entity_id=entity_id,
                    team=track.team,
                    relative_position=relative,
                    relative_velocity=track.velocity - observation.velocity,
                    distance_m=float(np.linalg.norm(relative)),
                    is_friendly=track.team is observer.team,
                    age_s=age,
                    # Confidence decays with staleness. A relayed track is never
                    # as good as a fresh measurement.
                    confidence=float(max(0.1, 0.75 - 0.15 * age)),
                    measured=False,
                    source="DATALINK",
                )
            )

        contacts.sort(key=lambda c: c.entity_id)
        overall = float(np.mean([c.confidence for c in contacts])) if contacts else 1.0

        return Observation(
            agent_id=observation.agent_id,
            entity_id=observation.entity_id,
            team=observation.team,
            simulation_time=observation.simulation_time,
            position=observation.position,
            velocity=observation.velocity,
            orientation=observation.orientation,
            angular_velocity=observation.angular_velocity,
            altitude=observation.altitude,
            speed=observation.speed,
            heading_deg=observation.heading_deg,
            contacts=contacts,
            confidence=overall,
        )

    def status(self) -> dict[str, object]:
        return {
            "report_rate_hz": self.comms.config.report_rate_hz,
            "report_interval_ticks": self.report_interval_ticks,
            "datalink_tracks": {entity_id: len(tracks) for entity_id, tracks in sorted(self._tracks.items())},
        }
