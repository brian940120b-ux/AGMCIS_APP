"""Reward (PHASE 12).

A reward that cannot be explained cannot be debugged. Every term here is a
named quantity, computed from measured state, multiplied by a weight from
``configs/training.yaml``, and reported separately — so when a policy learns
something strange the breakdown says which term paid for it.

The terms are flight- and navigation-quality only. There is deliberately no
weapon, engagement or targeting term: AIFCS models no such capability, and
rewarding one would be training a behaviour the platform must not have. A test
asserts the term names.

Nothing here can affect the simulation. The reward engine reads the state the
engine produced and returns numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from agents.base_agent import Observation
from core.config import RewardWeights
from core.world_state import EntityState, EntityStatus
from simulation.aircraft import ControlInputs

# Every term the engine can produce. Kept explicit so a typo in a weight name
# fails loudly instead of silently scoring zero.
TERM_NAMES = (
    "survival",
    "navigation",
    "formation",
    "mission",
    "coordination",
    "information",
    "crash_penalty",
    "collision_penalty",
    "energy_penalty",
    "control_smoothness",
)

# Endings that count as the aircraft having been lost.
_CRASH_REASONS = frozenset({"out_of_bounds", "disabled", "ground_impact", "removed"})


@dataclass(frozen=True)
class RewardConfig:
    """Thresholds the terms are measured against. Tuning, never behaviour."""

    # Navigation: metres of progress that count as a full step of progress.
    progress_scale_m: float = 200.0
    # Mission: how close counts as reaching the goal.
    goal_capture_radius_m: float = 800.0
    # Formation: station error at which the term reaches zero.
    formation_tolerance_m: float = 300.0
    # Collision: separation below which the penalty applies, and where it peaks.
    collision_radius_m: float = 200.0
    # Information: track age at which the picture counts as stale.
    track_age_tolerance_s: float = 3.0
    # Control smoothness: total control movement per step scoring zero.
    control_delta_for_zero: float = 2.0
    # Altitude band the aircraft is expected to stay inside.
    min_altitude_m: float = 100.0
    max_altitude_m: float = 19000.0


@dataclass
class RewardBreakdown:
    """One step's reward, and where every point of it came from."""

    total: float = 0.0
    terms: dict[str, float] = field(default_factory=dict)
    weighted: dict[str, float] = field(default_factory=dict)
    notes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": round(self.total, 5),
            "terms": {k: round(v, 5) for k, v in self.terms.items()},
            "weighted": {k: round(v, 5) for k, v in self.weighted.items()},
            "notes": self.notes,
        }


def _clamp01(value: float) -> float:
    return 0.0 if value < 0.0 else 1.0 if value > 1.0 else value


class RewardEngine:
    """Computes the reward for one policy-controlled unit, one step at a time."""

    def __init__(
        self,
        weights: RewardWeights | None = None,
        config: RewardConfig | None = None,
    ) -> None:
        self.weights = weights or RewardWeights()
        self.config = config or RewardConfig()
        self.reset()

    def reset(self) -> None:
        """Clear the per-episode memory the difference terms need."""
        self._previous_goal_distance: float | None = None
        self._previous_controls: ControlInputs | None = None
        self._goals_reached = 0

    @property
    def goals_reached(self) -> int:
        return self._goals_reached

    # -------------------------------------------------------------- the step

    def compute(
        self,
        *,
        entity: EntityState,
        observation: Observation,
        goal_position: np.ndarray | None,
        leader_offset_error_m: float | None,
        team_size: int,
        terminated_reason: str | None = None,
    ) -> RewardBreakdown:
        """Score one decision step.

        ``leader_offset_error_m`` is None for a unit with no leader, and the
        formation term then contributes nothing rather than punishing a unit
        for an order it never received.
        """
        terms: dict[str, float] = {}
        notes: dict[str, Any] = {}

        terms["survival"] = self._survival(entity, notes)
        terms["crash_penalty"] = self._crash(entity, terminated_reason, notes)
        terms["navigation"] = self._navigation(entity, goal_position, notes)
        terms["mission"] = self._mission(entity, goal_position, notes)
        terms["formation"] = self._formation(leader_offset_error_m, notes)
        terms["coordination"] = self._coordination(observation, team_size, notes)
        terms["information"] = self._information(observation, notes)
        terms["collision_penalty"] = self._collision(observation, notes)
        terms["energy_penalty"] = self._energy(entity, notes)
        terms["control_smoothness"] = self._smoothness(entity, notes)

        weights = self.weights.model_dump()
        weighted = {name: terms[name] * weights[name] for name in TERM_NAMES}

        self._previous_controls = entity.controls
        return RewardBreakdown(
            total=float(sum(weighted.values())),
            terms=terms,
            weighted=weighted,
            notes=notes,
        )

    # ---------------------------------------------------------------- terms

    def _survival(self, entity: EntityState, notes: dict[str, Any]) -> float:
        """A step spent alive and inside the altitude band.

        Per-step only. Losing the aircraft is a separate term: rolling both
        into one made a constant that paid every step regardless of what the
        policy did, and it drowned out the terms the policy could change.
        """
        if entity.status is not EntityStatus.ACTIVE:
            notes["survival"] = entity.status.value
            return 0.0
        altitude = entity.altitude
        if altitude < self.config.min_altitude_m or altitude > self.config.max_altitude_m:
            notes["survival"] = "outside the altitude band"
            return 0.0
        return 1.0

    def _crash(self, entity: EntityState, terminated_reason: str | None, notes: dict[str, Any]) -> float:
        """1 on the step the aircraft is lost, 0 otherwise. Weighted negative.

        Terminal rather than per-step, so a policy cannot profit by flying
        safely for a long time and then crashing: the penalty is paid in full
        whenever the episode ends that way.
        """
        if terminated_reason in _CRASH_REASONS:
            notes["crash"] = terminated_reason
            return 1.0
        if entity.status is not EntityStatus.ACTIVE:
            notes["crash"] = entity.status.value
            return 1.0
        return 0.0

    def _navigation(self, entity: EntityState, goal: np.ndarray | None, notes: dict[str, Any]) -> float:
        """Progress towards the goal, in units of ``progress_scale_m``.

        A difference term, not a distance term: rewarding closeness would pay a
        policy to sit still near the goal, while paying for *progress* only
        rewards actually covering ground.
        """
        if goal is None:
            notes["navigation"] = "no goal this episode"
            self._previous_goal_distance = None
            return 0.0

        distance = float(np.linalg.norm(np.asarray(goal, dtype=np.float64) - entity.position))
        if self._previous_goal_distance is None:
            # First step of an episode has nothing to compare against.
            self._previous_goal_distance = distance
            return 0.0

        progress = self._previous_goal_distance - distance
        self._previous_goal_distance = distance
        notes["goal_distance_m"] = round(distance, 1)
        return float(np.clip(progress / self.config.progress_scale_m, -1.0, 1.0))

    def _mission(self, entity: EntityState, goal: np.ndarray | None, notes: dict[str, Any]) -> float:
        """A one-off payment for actually arriving."""
        if goal is None:
            return 0.0
        distance = float(np.linalg.norm(np.asarray(goal, dtype=np.float64) - entity.position))
        if distance <= self.config.goal_capture_radius_m:
            self._goals_reached += 1
            notes["mission"] = "goal reached"
            return 1.0
        return 0.0

    def _formation(self, station_error_m: float | None, notes: dict[str, Any]) -> float:
        if station_error_m is None:
            notes["formation"] = "no leader assigned"
            return 0.0
        fraction = 1.0 - station_error_m / self.config.formation_tolerance_m
        notes["station_error_m"] = round(station_error_m, 1)
        return _clamp01(fraction)

    def _coordination(self, observation: Observation, team_size: int, notes: dict[str, Any]) -> float:
        """How much of its own team the unit currently knows the position of.

        Coordination is measured as awareness, not as proximity: a formation
        that has lost track of half itself is not coordinated however tight it
        looks. Teammates are seen through the sensor and the datalink, so this
        rewards using the link rather than flying blind.
        """
        others = max(team_size - 1, 0)
        if others == 0:
            notes["coordination"] = "no teammates in this scenario"
            return 0.0
        known = len({c.entity_id for c in observation.contacts if c.is_friendly})
        notes["teammates_held"] = f"{known}/{others}"
        return _clamp01(known / others)

    def _information(self, observation: Observation, notes: dict[str, Any]) -> float:
        """Quality and freshness of the picture the unit is holding."""
        if not observation.contacts:
            return 0.0
        age = max(c.age_s for c in observation.contacts)
        freshness = _clamp01(1.0 - age / self.config.track_age_tolerance_s)
        notes["max_track_age_s"] = round(age, 2)
        return 0.6 * _clamp01(observation.confidence) + 0.4 * freshness

    def _collision(self, observation: Observation, notes: dict[str, Any]) -> float:
        """How far inside the separation minimum the nearest contact is.

        Returns 0 when clear and 1 at zero separation, so the configured
        weight — which is negative — turns it into a penalty that grows the
        closer it gets rather than firing all at once.
        """
        nearest = observation.nearest_contact
        if nearest is None or nearest.distance_m >= self.config.collision_radius_m:
            return 0.0
        intrusion = 1.0 - nearest.distance_m / self.config.collision_radius_m
        notes["separation_m"] = round(nearest.distance_m, 1)
        return _clamp01(intrusion)

    def _energy(self, entity: EntityState, notes: dict[str, Any]) -> float:
        """Throttle actually used. Weighted negative, so it discourages waste."""
        throttle = abs(float(entity.controls.throttle))
        notes["throttle"] = round(throttle, 3)
        return _clamp01(throttle)

    def _smoothness(self, entity: EntityState, notes: dict[str, Any]) -> float:
        """1 for steady controls, 0 for thrashing them.

        Measured on the *applied* controls, which are what the safety layer let
        through — so a policy cannot earn smoothness by demanding something
        violent and relying on the rate limiter to tidy it up.
        """
        if self._previous_controls is None:
            return 1.0
        moved = sum(
            abs(getattr(entity.controls, channel) - getattr(self._previous_controls, channel))
            for channel in ("aileron", "elevator", "rudder", "throttle")
        )
        notes["control_movement"] = round(moved, 4)
        return _clamp01(1.0 - moved / self.config.control_delta_for_zero)
