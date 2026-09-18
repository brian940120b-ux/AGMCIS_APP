"""Sensor model (PHASE 5).

Until now an agent received the truth state verbatim. From here it receives an
**estimate**: range-limited, angle-limited, noisy, delayed, and sometimes
missing altogether.

This is the boundary the whole platform was shaped around. ``Observation``
already existed as a distinct type, so nothing in the agents changes — only what
fills it.

What the model does, in order:

1. **Detection** — a contact must be inside sensor range and inside the field of
   regard to be seen at all. Anything else simply is not in the list; the agent
   is not told that something is out there but unseen.
2. **Delay** — what is reported is where the contact *was*, one latency ago, not
   where it is now.
3. **Dropout** — a detection can be missed. A previously seen contact is then
   *coasted* from its last known track for a while, with confidence decaying,
   which is what a real tracker does rather than forgetting instantly.
4. **Noise** — Gaussian error that grows with range, because angular error
   projects into a larger cross-range error the further away the contact is.
5. **Confidence** — derived from range, staleness and whether the track was
   measured or coasted. It is computed, never asserted.

Determinism: all randomness comes from a generator seeded with the run seed, and
entities are always processed in sorted order, so the same seed reproduces the
same dropouts and the same noise.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from agents.base_agent import ContactView, Observation
from core.world_state import EntityState, EntityStatus, Team, WorldState


@dataclass(frozen=True)
class SensorConfig:
    """Abstract sensing performance. Fictional, like everything else here."""

    enabled: bool = True

    # Detection envelope.
    max_range_m: float = 80_000.0
    # Half-angle from the nose; 180 means all-round coverage.
    #
    # The default is deliberately narrower than all-round. On its own that
    # breaks formation flying — a wingman loses its leader the moment it
    # overshoots — but the PHASE 6 datalink covers exactly that gap, and the
    # coupling between the two is what makes this interesting to study.
    field_of_regard_deg: float = 120.0

    # Reporting latency, in seconds.
    latency_s: float = 0.2

    # Per-observation probability that a detection is missed.
    dropout_probability: float = 0.05
    # How long a missed contact is coasted from its last known track.
    track_memory_s: float = 3.0

    # Measurement noise. The per-kilometre term models angular error projecting
    # into cross-range error with distance.
    position_noise_base_m: float = 20.0
    position_noise_per_km_m: float = 2.0
    velocity_noise_mps: float = 3.0

    # Ownship estimate. Small but not perfect — an aircraft does not know its own
    # state exactly either.
    ownship_position_noise_m: float = 5.0
    ownship_velocity_noise_mps: float = 0.5


@dataclass
class Track:
    """A contact the sensor is holding, measured or coasted.

    ``measured_position`` is the last actual measurement. A coasted estimate is
    derived from it on demand rather than integrated step by step, so the dead
    reckoning does not depend on how often ``observe`` happens to be called.
    """

    entity_id: str
    team: Team
    measured_position: np.ndarray
    velocity: np.ndarray
    last_measured_time: float
    confidence: float

    def estimate(self, now: float) -> np.ndarray:
        """Where the contact is believed to be, dead-reckoned from the last fix."""
        return self.measured_position + self.velocity * max(0.0, now - self.last_measured_time)


@dataclass
class _Snapshot:
    """Positions and velocities at one past tick, for the latency buffer."""

    simulation_time: float
    states: dict[str, tuple[np.ndarray, np.ndarray, Team, EntityStatus]] = field(default_factory=dict)


class SensorModel:
    """Turns truth into what an agent is allowed to know."""

    def __init__(self, config: SensorConfig | None = None, seed: int = 0, tick_rate_hz: int = 60) -> None:
        self.config = config or SensorConfig()
        self.tick_rate_hz = tick_rate_hz
        self._rng = np.random.default_rng(seed)
        self._seed = seed

        # One snapshot per tick, just deep enough to cover the configured latency.
        depth = max(1, round(self.config.latency_s * tick_rate_hz) + 1)
        self._history: deque[_Snapshot] = deque(maxlen=depth)
        self._tracks: dict[str, dict[str, Track]] = {}

    # ------------------------------------------------------------- lifecycle

    def reset(self, seed: int | None = None) -> None:
        """Clear tracks and history and restart the noise sequence."""
        self._seed = self._seed if seed is None else seed
        self._rng = np.random.default_rng(self._seed)
        self._history.clear()
        self._tracks.clear()

    def record(self, world: WorldState) -> None:
        """Capture the current truth for later, delayed, reporting."""
        snapshot = _Snapshot(simulation_time=world.simulation_time)
        for entity_id in sorted(world.entities):
            entity = world.entities[entity_id]
            snapshot.states[entity_id] = (
                entity.position.copy(),
                entity.velocity.copy(),
                entity.team,
                entity.status,
            )
        self._history.append(snapshot)

    def _delayed_snapshot(self) -> _Snapshot | None:
        """The oldest snapshot still held — that is the latency the buffer models."""
        return self._history[0] if self._history else None

    # ------------------------------------------------------------ observing

    def observe(self, agent_id: str, observer: EntityState, world: WorldState) -> Observation:
        """Build one agent's degraded view of the world."""
        if not self.config.enabled:
            return self._perfect_observation(agent_id, observer, world)

        config = self.config
        rng = self._rng
        tracks = self._tracks.setdefault(observer.id, {})

        # Ownship: small error, but not zero.
        own_position = observer.position + rng.normal(0.0, config.ownship_position_noise_m, 3)
        own_velocity = observer.velocity + rng.normal(0.0, config.ownship_velocity_noise_mps, 3)

        snapshot = self._delayed_snapshot()
        now = world.simulation_time

        contacts: list[ContactView] = []
        seen_this_cycle: set[str] = set()

        if snapshot is not None:
            for entity_id in sorted(snapshot.states):
                if entity_id == observer.id:
                    continue
                position, velocity, team, status = snapshot.states[entity_id]
                if status is not EntityStatus.ACTIVE:
                    continue

                relative = position - observer.position
                distance = float(np.linalg.norm(relative))

                if distance > config.max_range_m or not self._within_field_of_regard(
                    observer, relative, distance
                ):
                    continue

                # A draw is taken for every candidate in range, whether or not it
                # ends up dropping, so the random sequence does not depend on the
                # outcome of earlier draws.
                dropped = bool(rng.random() < config.dropout_probability)
                if dropped:
                    continue

                sigma = config.position_noise_base_m + config.position_noise_per_km_m * (distance / 1000.0)
                measured_position = position + rng.normal(0.0, sigma, 3)
                measured_velocity = velocity + rng.normal(0.0, config.velocity_noise_mps, 3)

                tracks[entity_id] = Track(
                    entity_id=entity_id,
                    team=team,
                    measured_position=measured_position,
                    velocity=measured_velocity,
                    last_measured_time=now,
                    confidence=self._confidence(distance, 0.0),
                )
                seen_this_cycle.add(entity_id)

        # Coast anything not measured this cycle, and forget stale tracks.
        for entity_id in list(tracks):
            track = tracks[entity_id]
            age = now - track.last_measured_time
            if entity_id not in seen_this_cycle and age > config.track_memory_s:
                del tracks[entity_id]
                continue

            estimated = track.estimate(now)
            distance = float(np.linalg.norm(estimated - own_position))
            track.confidence = self._confidence(distance, age)

            relative_position = estimated - own_position
            contacts.append(
                ContactView(
                    entity_id=track.entity_id,
                    team=track.team,
                    relative_position=relative_position,
                    relative_velocity=track.velocity - own_velocity,
                    distance_m=float(np.linalg.norm(relative_position)),
                    is_friendly=track.team is observer.team,
                    age_s=age,
                    confidence=track.confidence,
                    measured=entity_id in seen_this_cycle,
                )
            )

        contacts.sort(key=lambda c: c.entity_id)
        overall = float(np.mean([c.confidence for c in contacts])) if contacts else 1.0

        return self._build(agent_id, observer, world, own_position, own_velocity, contacts, overall)

    def _within_field_of_regard(self, observer: EntityState, relative: np.ndarray, distance: float) -> bool:
        """True when the contact lies inside the sensor's angular coverage."""
        if self.config.field_of_regard_deg >= 180.0:
            return True
        if distance < 1e-6:
            return True

        # Nose direction in the ENU horizontal plane, from yaw.
        yaw = float(observer.orientation[2])
        nose = np.array([math.sin(yaw), math.cos(yaw), 0.0])
        horizontal = np.array([relative[0], relative[1], 0.0])
        horizontal_range = float(np.linalg.norm(horizontal))
        if horizontal_range < 1e-6:
            return True

        cosine = float(np.dot(nose, horizontal / horizontal_range))
        angle_deg = math.degrees(math.acos(max(-1.0, min(1.0, cosine))))
        return angle_deg <= self.config.field_of_regard_deg

    def _confidence(self, distance_m: float, age_s: float) -> float:
        """Quality of a track, from range and staleness. Never asserted, always derived."""
        range_term = 1.0 - min(1.0, distance_m / max(self.config.max_range_m, 1.0)) * 0.5
        age_term = 1.0 - min(1.0, age_s / max(self.config.track_memory_s, 1e-6)) * 0.7
        return float(max(0.0, min(1.0, range_term * age_term)))

    # --------------------------------------------------------------- helpers

    def _perfect_observation(self, agent_id: str, observer: EntityState, world: WorldState) -> Observation:
        """Undegraded view, used when sensing is disabled for a study."""
        contacts = [
            ContactView(
                entity_id=other.id,
                team=other.team,
                relative_position=other.position - observer.position,
                relative_velocity=other.velocity - observer.velocity,
                distance_m=float(np.linalg.norm(other.position - observer.position)),
                is_friendly=other.team is observer.team,
                age_s=0.0,
                confidence=1.0,
                measured=True,
            )
            for other in world.entities.values()
            if other.id != observer.id and other.status is EntityStatus.ACTIVE
        ]
        contacts.sort(key=lambda c: c.entity_id)
        return self._build(
            agent_id, observer, world, observer.position.copy(), observer.velocity.copy(), contacts, 1.0
        )

    @staticmethod
    def _build(
        agent_id: str,
        observer: EntityState,
        world: WorldState,
        position: np.ndarray,
        velocity: np.ndarray,
        contacts: list[ContactView],
        confidence: float,
    ) -> Observation:
        speed = float(np.linalg.norm(velocity))
        heading = (
            float(np.degrees(np.arctan2(velocity[0], velocity[1])) % 360.0)
            if speed > 1e-6
            else observer.heading_deg
        )
        return Observation(
            agent_id=agent_id,
            entity_id=observer.id,
            team=observer.team,
            simulation_time=world.simulation_time,
            position=position,
            velocity=velocity,
            orientation=observer.orientation.copy(),
            angular_velocity=observer.angular_velocity.copy(),
            altitude=float(position[2]),
            speed=speed,
            heading_deg=heading,
            contacts=contacts,
            confidence=confidence,
        )

    def status(self) -> dict[str, object]:
        return {
            "enabled": self.config.enabled,
            "max_range_m": self.config.max_range_m,
            "field_of_regard_deg": self.config.field_of_regard_deg,
            "latency_s": self.config.latency_s,
            "dropout_probability": self.config.dropout_probability,
            "track_memory_s": self.config.track_memory_s,
            "tracked_contacts": {observer: len(tracks) for observer, tracks in sorted(self._tracks.items())},
        }
