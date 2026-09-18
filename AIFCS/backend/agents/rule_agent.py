"""Rule-based pilot agent (PHASE 3).

Selects a behaviour from measured conditions, in a fixed priority order, and
flies it through the guidance loops. There is no learning and no hidden state:
given the same observation, it makes the same decision.

Priority, highest first:

1. AVOID    — another unit is inside the collision radius.
2. FORMATION— a leader is assigned; hold station on it.
3. PATROL   — a route is assigned; fly it and cycle.
4. NAVIGATE — a single waypoint is assigned.
5. HOLD     — nothing assigned; maintain the current track.

Every reason code is emitted only when the condition it names was computed and
met, so a decision record can always be checked against the data behind it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from agents.base_agent import (
    Action,
    BaseAgent,
    Behaviour,
    Decision,
    Observation,
    ReasonCode,
)
from agents.guidance import (
    GuidanceGains,
    GuidanceState,
    bearing_deg,
    compute_controls,
    heading_error_deg,
)
from core.world_state import Team


@dataclass
class RuleAgentConfig:
    """Behaviour tuning. Loaded from ``configs/agents.yaml``."""

    waypoint_capture_radius_m: float = 800.0
    formation_spacing_m: float = 850.0
    formation_station_tolerance_m: float = 150.0
    collision_radius_m: float = 200.0
    cruise_speed_mps: float = 220.0
    default_altitude_m: float | None = None
    heading_error_threshold_deg: float = 15.0
    altitude_error_threshold_m: float = 100.0
    speed_error_threshold_mps: float = 15.0
    # Seconds between decisions, matching the agent manager's rate. The
    # integrator needs it to accumulate error correctly.
    decision_interval_s: float = 0.1
    gains: GuidanceGains = field(default_factory=GuidanceGains)


@dataclass
class RouteAssignment:
    """A list of waypoints, optionally cycled."""

    waypoints: list[np.ndarray] = field(default_factory=list)
    loop: bool = True
    index: int = 0

    @property
    def active(self) -> np.ndarray | None:
        if not self.waypoints or self.index >= len(self.waypoints):
            return None
        return self.waypoints[self.index]

    @property
    def complete(self) -> bool:
        return bool(self.waypoints) and self.index >= len(self.waypoints)

    def advance(self) -> None:
        self.index += 1
        if self.loop and self.index >= len(self.waypoints):
            self.index = 0

    def reset(self) -> None:
        self.index = 0


@dataclass
class FormationAssignment:
    """Station-keeping on a leader, offset in the leader's local frame."""

    leader_id: str
    offset: np.ndarray


class RuleAgent(BaseAgent):
    """Deterministic pilot: navigation, formation keeping and collision avoidance."""

    def __init__(
        self,
        agent_id: str,
        entity_id: str,
        team: Team,
        config: RuleAgentConfig | None = None,
        route: RouteAssignment | None = None,
        formation: FormationAssignment | None = None,
    ) -> None:
        super().__init__(agent_id, entity_id, team)
        self.config = config or RuleAgentConfig()
        self.route = route or RouteAssignment()
        self.formation = formation
        # Captured on the first observation so HOLD has something to hold.
        self._hold_altitude_m: float | None = None
        self._hold_heading_deg: float | None = None
        self._target: dict[str, float] = {}
        self._guidance = GuidanceState()

    # ------------------------------------------------------------- thinking

    def think(self, observation: Observation) -> Decision:
        if self._hold_altitude_m is None:
            self._hold_altitude_m = observation.altitude
            self._hold_heading_deg = observation.heading_deg

        reasons: list[ReasonCode] = []
        metrics: dict[str, Any] = {}

        behaviour, confidence = self._select(observation, reasons, metrics)
        self._annotate_tracking(observation, reasons, metrics)

        return Decision(
            agent_id=self.agent_id,
            entity_id=self.entity_id,
            simulation_time=observation.simulation_time,
            tick=metrics.pop("_tick", 0),
            behaviour=behaviour,
            confidence=confidence,
            reason_codes=reasons,
            observation_summary=observation.summary(),
            metrics=metrics,
        )

    def _select(
        self, observation: Observation, reasons: list[ReasonCode], metrics: dict[str, Any]
    ) -> tuple[Behaviour, float]:
        """Pick a behaviour in priority order and score how clear-cut it was."""
        config = self.config

        # --- 1. Collision avoidance.
        nearest = observation.nearest_contact
        if nearest is not None and nearest.distance_m < config.collision_radius_m:
            reasons.append(ReasonCode.COLLISION_RISK)
            metrics["conflict_with"] = nearest.entity_id
            metrics["separation_m"] = round(nearest.distance_m, 1)
            # The deeper inside the radius, the less ambiguous the call is.
            intrusion = 1.0 - nearest.distance_m / config.collision_radius_m
            return Behaviour.AVOID, float(np.clip(0.6 + 0.4 * intrusion, 0.0, 1.0))

        # --- 2. Formation keeping.
        if self.formation is not None:
            leader = next((c for c in observation.contacts if c.entity_id == self.formation.leader_id), None)
            if leader is None:
                reasons.append(ReasonCode.LEADER_UNAVAILABLE)
            else:
                reasons.append(ReasonCode.FORMATION_ASSIGNED)
                station_error = float(np.linalg.norm(leader.relative_position - self.formation.offset))
                metrics["leader"] = self.formation.leader_id
                metrics["station_error_m"] = round(station_error, 1)

                if station_error > config.formation_station_tolerance_m:
                    reasons.append(ReasonCode.FORMATION_SEPARATION_HIGH)
                else:
                    reasons.append(ReasonCode.FORMATION_IN_STATION)

                # Confidence falls as the station error grows: a unit far out of
                # position is less certainly "in formation".
                ratio = station_error / max(config.formation_spacing_m, 1.0)
                return Behaviour.FORMATION, float(np.clip(1.0 - 0.4 * ratio, 0.3, 1.0))

        # --- 3/4. Route following.
        if self.route.waypoints:
            if self.route.complete:
                reasons.append(ReasonCode.ROUTE_COMPLETE)
                return Behaviour.HOLD, 1.0

            target = self.route.active
            assert target is not None  # guarded by the branches above
            distance = float(np.linalg.norm(target[:2] - observation.position[:2]))
            metrics["waypoint_index"] = self.route.index
            metrics["waypoint_distance_m"] = round(distance, 1)

            if distance < config.waypoint_capture_radius_m:
                reasons.append(ReasonCode.WAYPOINT_REACHED)
                self.route.advance()
                metrics["waypoint_index"] = self.route.index
            else:
                reasons.append(ReasonCode.WAYPOINT_ACTIVE)

            behaviour = Behaviour.PATROL if len(self.route.waypoints) > 1 else Behaviour.NAVIGATE
            # Closer to the leg's end, the less the choice could be anything else.
            closeness = 1.0 - min(distance / 20000.0, 1.0)
            return behaviour, float(np.clip(0.65 + 0.35 * closeness, 0.0, 1.0))

        # --- 5. Nothing assigned.
        reasons.append(ReasonCode.NO_ROUTE_ASSIGNED)
        return Behaviour.HOLD, 1.0

    def _annotate_tracking(
        self, observation: Observation, reasons: list[ReasonCode], metrics: dict[str, Any]
    ) -> None:
        """Record how far the aircraft is from the targets it is flying to."""
        target = self._targets_for(observation)
        self._target = target

        altitude_err = target["altitude"] - observation.altitude
        heading_err = heading_error_deg(target["heading"], observation.heading_deg)
        speed_err = target["speed"] - observation.speed

        metrics["altitude_error_m"] = round(altitude_err, 1)
        metrics["heading_error_deg"] = round(heading_err, 1)
        metrics["speed_error_mps"] = round(speed_err, 1)

        if altitude_err > self.config.altitude_error_threshold_m:
            reasons.append(ReasonCode.ALTITUDE_BELOW_TARGET)
        elif altitude_err < -self.config.altitude_error_threshold_m:
            reasons.append(ReasonCode.ALTITUDE_ABOVE_TARGET)

        if abs(heading_err) > self.config.heading_error_threshold_deg:
            reasons.append(ReasonCode.HEADING_ERROR_LARGE)

        if speed_err > self.config.speed_error_threshold_mps:
            reasons.append(ReasonCode.SPEED_BELOW_TARGET)
        elif speed_err < -self.config.speed_error_threshold_mps:
            reasons.append(ReasonCode.SPEED_ABOVE_TARGET)

    # ------------------------------------------------------------- targeting

    def _targets_for(self, observation: Observation) -> dict[str, float]:
        """Heading, altitude and speed the aircraft should currently fly."""
        config = self.config
        hold_altitude = (
            config.default_altitude_m
            if config.default_altitude_m is not None
            else (self._hold_altitude_m if self._hold_altitude_m is not None else observation.altitude)
        )
        hold_heading = (
            self._hold_heading_deg if self._hold_heading_deg is not None else observation.heading_deg
        )

        # Collision avoidance: turn away from the conflict and climb over it.
        nearest = observation.nearest_contact
        if nearest is not None and nearest.distance_m < config.collision_radius_m:
            conflict_bearing = bearing_deg(np.zeros(3), nearest.relative_position)
            away = (conflict_bearing + 180.0) % 360.0
            return {
                "heading": away,
                "altitude": observation.altitude + 300.0,
                "speed": config.cruise_speed_mps,
            }

        # Formation: match the leader's track, corrected for station error.
        #
        # Pointing straight at the station would be unstable close in — a few
        # metres of error swings the demanded heading wildly. Instead the
        # wingman flies the leader's heading and corrects with the cross-track
        # component, and closes the along-track gap with speed.
        if self.formation is not None:
            leader = next((c for c in observation.contacts if c.entity_id == self.formation.leader_id), None)
            if leader is not None:
                station = leader.relative_position - self.formation.offset

                leader_velocity = observation.velocity + leader.relative_velocity
                leader_ground_speed = float(np.linalg.norm(leader_velocity[:2]))
                if leader_ground_speed > 1.0:
                    leader_heading = float(
                        np.degrees(np.arctan2(leader_velocity[0], leader_velocity[1])) % 360.0
                    )
                    forward = leader_velocity[:2] / leader_ground_speed
                else:
                    leader_heading = observation.heading_deg
                    forward = np.array(
                        [np.sin(np.radians(leader_heading)), np.cos(np.radians(leader_heading))]
                    )

                # Right-hand normal to the leader's track.
                right = np.array([forward[1], -forward[0]])
                along_track = float(np.dot(station[:2], forward))
                cross_track = float(np.dot(station[:2], right))

                heading_correction = float(np.clip(cross_track * 0.06, -45.0, 45.0))
                closure = float(np.clip(along_track * 0.10, -40.0, 40.0))

                return {
                    "heading": (leader_heading + heading_correction) % 360.0,
                    "altitude": observation.altitude + float(station[2]),
                    "speed": max(leader_ground_speed, 80.0) + closure,
                }

        # Route: fly to the active waypoint.
        target = self.route.active
        if target is not None:
            return {
                "heading": bearing_deg(observation.position, target),
                "altitude": float(target[2]),
                "speed": config.cruise_speed_mps,
            }

        return {"heading": hold_heading, "altitude": hold_altitude, "speed": config.cruise_speed_mps}

    # ------------------------------------------------------------------ acting

    def act(self, observation: Observation, decision: Decision) -> Action:
        target = self._target or self._targets_for(observation)

        controls = compute_controls(
            orientation=observation.orientation,
            angular_velocity=observation.angular_velocity,
            altitude_m=observation.altitude,
            speed_mps=observation.speed,
            heading_deg=observation.heading_deg,
            target_heading_deg=target["heading"],
            target_altitude_m=target["altitude"],
            target_speed_mps=target["speed"],
            gains=self.config.gains,
            state=self._guidance,
            dt=self.config.decision_interval_s,
        )

        return Action(
            controls=controls,
            target_altitude_m=target["altitude"],
            target_speed_mps=target["speed"],
            target_heading_deg=target["heading"],
        )

    def reset(self) -> None:
        super().reset()
        self.route.reset()
        self._hold_altitude_m = None
        self._hold_heading_deg = None
        self._target = {}
        self._guidance.reset()
