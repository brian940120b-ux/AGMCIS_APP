"""Measurements taken from a recording (PHASE 9).

This module only measures. It answers "what happened" — how far a unit flew,
how hard it worked its controls, how well it held station — and has no opinion
about whether any of it was good. Turning measurements into a score is the
scoring engine's job, and keeping the two apart is what lets the scoring
standard change without touching the measurement of a run.

Everything here is derived from a recording, never from a live engine. A run
scored a week later scores identically.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import median
from typing import Any

from replay.reader import Recording

# Reason codes that mark route progress, counted rather than interpreted.
_WAYPOINT_REACHED = "WAYPOINT_REACHED"
_ROUTE_COMPLETE = "ROUTE_COMPLETE"
_ROUTE_ACTIVE_CODES = frozenset({"WAYPOINT_ACTIVE", _WAYPOINT_REACHED, _ROUTE_COMPLETE})

# Events that count against the safety term.
_SAFETY_EVENT_TYPES = frozenset({"ACTION_REJECTED", "ENTITY_OUT_OF_BOUNDS", "COLLISION"})

_CONTROL_CHANNELS = ("elevator", "aileron", "rudder", "throttle")


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


@dataclass
class EntityMetrics:
    """What one unit actually did over a run."""

    entity_id: str
    team: str = ""
    # How long the run lasted, so a term can ask what the run had time for.
    run_duration_s: float = 0.0

    # Presence
    frames: int = 0
    frames_active: int = 0
    final_status: str = ""
    ended_active: bool = False

    # Movement
    distance_travelled_m: float = 0.0
    mean_speed_mps: float = 0.0
    min_altitude_m: float = 0.0
    max_altitude_m: float = 0.0

    # Tracking, from the agent's own recorded decisions
    decisions: int = 0
    mean_abs_heading_error_deg: float = 0.0
    mean_abs_altitude_error_m: float = 0.0
    had_route: bool = False
    waypoints_reached: int = 0
    route_completions: int = 0

    # Formation, only for a unit that was actually assigned a leader
    had_leader: bool = False
    formation_samples: int = 0
    mean_station_error_m: float = 0.0
    # A wingman spawned away from station has to fly there first. Scoring the
    # whole run measures the join-up, not the station keeping, so the settled
    # tail is kept separately. `station_errors_settled` stays out of to_dict:
    # it is raw samples for the scoring engine, not a reported figure.
    mean_station_error_settled_m: float = 0.0
    median_station_error_settled_m: float = 0.0
    station_errors_settled: list[float] = field(default_factory=list, repr=False)

    # Safety. A command the controller refused outright and a demand the
    # envelope protection eased back are different events with the same name on
    # the bus, so they are told apart here by whether the command was accepted.
    rejected_commands: int = 0
    envelope_interventions: int = 0
    out_of_bounds_events: int = 0
    collision_events: int = 0
    # Interventions fire once per control tick, so the count alone measures how
    # long a manoeuvre lasted. As a fraction of the run's ticks it measures how
    # much of the run was flown against the limits, which is the real question.
    control_ticks: int = 0

    # Control usage
    mean_control_rate_per_s: float = 0.0
    max_control_rate_per_s: float = 0.0

    # Picture quality
    mean_contacts: float = 0.0
    mean_observation_confidence: float = 0.0
    mean_track_age_s: float = 0.0

    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def active_fraction(self) -> float:
        return self.frames_active / self.frames if self.frames else 0.0

    @property
    def safety_events(self) -> int:
        """Genuine failures. Envelope interventions are deliberately not here."""
        return self.rejected_commands + self.out_of_bounds_events + self.collision_events

    @property
    def envelope_intervention_fraction(self) -> float:
        """Share of control ticks flown with the envelope protection active."""
        return self.envelope_interventions / self.control_ticks if self.control_ticks else 0.0

    def to_dict(self) -> dict[str, float | int | str | bool]:
        return {
            "entity_id": self.entity_id,
            "team": self.team,
            "run_duration_s": round(self.run_duration_s, 3),
            "frames": self.frames,
            "frames_active": self.frames_active,
            "active_fraction": round(self.active_fraction, 4),
            "final_status": self.final_status,
            "ended_active": self.ended_active,
            "distance_travelled_m": round(self.distance_travelled_m, 1),
            "mean_speed_mps": round(self.mean_speed_mps, 2),
            "min_altitude_m": round(self.min_altitude_m, 1),
            "max_altitude_m": round(self.max_altitude_m, 1),
            "decisions": self.decisions,
            "mean_abs_heading_error_deg": round(self.mean_abs_heading_error_deg, 2),
            "mean_abs_altitude_error_m": round(self.mean_abs_altitude_error_m, 2),
            "had_route": self.had_route,
            "waypoints_reached": self.waypoints_reached,
            "route_completions": self.route_completions,
            "had_leader": self.had_leader,
            "mean_station_error_m": round(self.mean_station_error_m, 2),
            "mean_station_error_settled_m": round(self.mean_station_error_settled_m, 2),
            "median_station_error_settled_m": round(self.median_station_error_settled_m, 2),
            "formation_samples": self.formation_samples,
            "rejected_commands": self.rejected_commands,
            "envelope_interventions": self.envelope_interventions,
            "envelope_intervention_fraction": round(self.envelope_intervention_fraction, 5),
            "out_of_bounds_events": self.out_of_bounds_events,
            "collision_events": self.collision_events,
            "safety_events": self.safety_events,
            "control_ticks": self.control_ticks,
            "mean_control_rate_per_s": round(self.mean_control_rate_per_s, 4),
            "max_control_rate_per_s": round(self.max_control_rate_per_s, 4),
            "mean_contacts": round(self.mean_contacts, 2),
            "mean_observation_confidence": round(self.mean_observation_confidence, 4),
            "mean_track_age_s": round(self.mean_track_age_s, 3),
        }


class _Accumulator:
    """Running totals for one entity while the recording is scanned once."""

    def __init__(self, entity_id: str) -> None:
        self.m = EntityMetrics(entity_id=entity_id)
        self.speeds: list[float] = []
        self.heading_errors: list[float] = []
        self.altitude_errors: list[float] = []
        self.station_errors: list[tuple[float, float]] = []  # (simulation_time, error_m)
        self.control_rates: list[float] = []
        self.contacts: list[float] = []
        self.confidences: list[float] = []
        self.track_ages: list[float] = []
        self.altitudes: list[float] = []
        self.last_position: list[float] | None = None
        self.last_controls: dict[str, float] | None = None
        self.last_time: float | None = None

    def finish(self, settle_fraction: float, duration_s: float) -> EntityMetrics:
        m = self.m
        m.run_duration_s = duration_s
        m.mean_speed_mps = _mean(self.speeds)
        m.min_altitude_m = min(self.altitudes) if self.altitudes else 0.0
        m.max_altitude_m = max(self.altitudes) if self.altitudes else 0.0
        m.mean_abs_heading_error_deg = _mean(self.heading_errors)
        m.mean_abs_altitude_error_m = _mean(self.altitude_errors)
        errors = [e for _, e in self.station_errors]
        m.mean_station_error_m = _mean(errors)
        m.formation_samples = len(errors)

        # The settled tail: everything after the join-up window. If the run was
        # too short to have one, fall back to every sample rather than to none,
        # so a short run is scored on what it did rather than on nothing.
        cutoff = duration_s * (1.0 - settle_fraction)
        settled = [e for t, e in self.station_errors if t >= cutoff]
        if not settled:
            settled = errors
        m.station_errors_settled = settled
        m.mean_station_error_settled_m = _mean(settled)
        m.median_station_error_settled_m = median(settled) if settled else 0.0
        m.mean_control_rate_per_s = _mean(self.control_rates)
        m.max_control_rate_per_s = max(self.control_rates) if self.control_rates else 0.0
        m.mean_contacts = _mean(self.contacts)
        m.mean_observation_confidence = _mean(self.confidences)
        m.mean_track_age_s = _mean(self.track_ages)
        return m


def compute_metrics(
    recording: Recording, *, formation_settle_fraction: float = 0.5
) -> dict[str, EntityMetrics]:
    """Measure every entity in a recording in a single pass.

    ``formation_settle_fraction`` says how much of the tail of the run counts
    as settled formation flying; the rest is treated as join-up. It is a
    measurement window, not a judgement — what counts as a *good* station error
    is the scoring engine's decision.
    """
    accumulators: dict[str, _Accumulator] = {}
    duration_s = recording.duration_s
    # The controller runs every physics tick, not every recorded frame.
    control_ticks = round(duration_s * recording.tick_rate_hz)

    def accumulator(entity_id: str) -> _Accumulator:
        if entity_id not in accumulators:
            accumulators[entity_id] = _Accumulator(entity_id)
        return accumulators[entity_id]

    for frame in recording.frames:
        frame_time = float(frame.get("simulation_time", 0.0))

        for entity in frame.get("entities", []):
            acc = accumulator(str(entity["id"]))
            m = acc.m
            m.team = str(entity.get("team", m.team))
            m.frames += 1
            status = str(entity.get("status", ""))
            m.final_status = status
            if status == "ACTIVE":
                m.frames_active += 1

            speed = float(entity.get("speed", 0.0))
            acc.speeds.append(speed)
            acc.altitudes.append(float(entity.get("altitude", 0.0)))

            position = [float(v) for v in entity.get("position", [0.0, 0.0, 0.0])]
            if acc.last_position is not None:
                m.distance_travelled_m += math.dist(position, acc.last_position)
            acc.last_position = position

            # Control movement per second, which is what "smooth" means here.
            controls = {k: float(v) for k, v in (entity.get("controls") or {}).items()}
            if acc.last_controls is not None and acc.last_time is not None:
                dt = frame_time - acc.last_time
                if dt > 0:
                    moved = sum(
                        abs(controls.get(c, 0.0) - acc.last_controls.get(c, 0.0)) for c in _CONTROL_CHANNELS
                    )
                    acc.control_rates.append(moved / dt)
            acc.last_controls = controls
            acc.last_time = frame_time

        for decision in frame.get("decisions", []):
            acc = accumulator(str(decision.get("entity_id", "")))
            m = acc.m
            m.decisions += 1

            metrics = decision.get("metrics") or {}
            if "heading_error_deg" in metrics:
                acc.heading_errors.append(abs(float(metrics["heading_error_deg"])))
            if "altitude_error_m" in metrics:
                acc.altitude_errors.append(abs(float(metrics["altitude_error_m"])))
            if "station_error_m" in metrics:
                acc.station_errors.append(
                    (
                        float(decision.get("simulation_time", frame_time)),
                        abs(float(metrics["station_error_m"])),
                    )
                )
            if metrics.get("leader"):
                m.had_leader = True

            codes = decision.get("reason_codes") or []
            # A wingman is never given waypoints. Route progress is meaningless
            # for it, and scoring it zero would punish it for an order it never
            # received — so record whether a route existed at all.
            if "waypoint_index" in metrics or _ROUTE_ACTIVE_CODES.intersection(codes):
                m.had_route = True
            if _WAYPOINT_REACHED in codes:
                m.waypoints_reached += 1
            if _ROUTE_COMPLETE in codes:
                m.route_completions += 1
            # A unit told to hold station has a leader even on the ticks where
            # the leader was not visible and no station error could be measured.
            if "FORMATION_ASSIGNED" in codes or "LEADER_UNAVAILABLE" in codes:
                m.had_leader = True

            observation = decision.get("observation") or {}
            if "contacts" in observation:
                acc.contacts.append(float(observation["contacts"]))
            if "confidence" in observation:
                acc.confidences.append(float(observation["confidence"]))
            if "max_track_age_s" in observation:
                acc.track_ages.append(float(observation["max_track_age_s"]))

        for event in frame.get("events", []):
            event_type = str(event.get("type", ""))
            if event_type not in _SAFETY_EVENT_TYPES:
                continue
            entity_id = event.get("entity_id")
            if not entity_id:
                continue
            m = accumulator(str(entity_id)).m
            if event_type == "ACTION_REJECTED":
                # The bus uses one event type for both. `accepted` is what
                # separates "the controller refused this" from "the envelope
                # protection eased it back", and only the first is a failure.
                data = event.get("data") or {}
                if data.get("accepted", False):
                    m.envelope_interventions += 1
                else:
                    m.rejected_commands += 1
            elif event_type == "ENTITY_OUT_OF_BOUNDS":
                m.out_of_bounds_events += 1
            else:
                m.collision_events += 1

    results = {}
    for entity_id, acc in accumulators.items():
        metrics = acc.finish(formation_settle_fraction, duration_s)
        metrics.control_ticks = control_ticks
        metrics.ended_active = metrics.final_status == "ACTIVE"
        results[entity_id] = metrics
    return results
