"""Scoring (PHASE 9).

Scores live **outside** the simulation engine. Nothing here can be reached from
a tick: the scoring engine takes a finished recording and returns numbers. That
separation is the point — a score can be recomputed with different weights, or
a new term added, without any risk of changing how an aircraft flies.

Every term is a fraction in 0..1 multiplied by a weight from YAML, and every
term reports the measurement it came from. A score you cannot explain is not
worth having, so ``breakdown`` always names the quantity, the threshold it was
judged against and the resulting fraction.

Terms are flight- and mission-quality measures. There is deliberately no
weapon, engagement or targeting term: AIFCS models none, and scoring one would
imply a capability the platform does not have and must not acquire.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from core.config import ScoreThresholds, ScoreWeights, ScoringSettings
from core.logging_config import get_logger
from replay.reader import Recording
from scoring.metrics import EntityMetrics, compute_metrics

log = get_logger("scoring")

TERM_NAMES = ("survival", "navigation", "formation", "safety", "efficiency", "information")


def _clamp01(value: float) -> float:
    return 0.0 if value < 0.0 else 1.0 if value > 1.0 else value


def _decay(measured: float, threshold: float) -> float:
    """1.0 at zero error, 0.0 once the measurement reaches the threshold."""
    if threshold <= 0:
        return 0.0
    return _clamp01(1.0 - measured / threshold)


@dataclass
class ScoreTerm:
    """One scored dimension, with the evidence behind it."""

    name: str
    fraction: float
    weight: float
    points: float
    applicable: bool = True
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "fraction": round(self.fraction, 4),
            "weight": round(self.weight, 3),
            "points": round(self.points, 3),
            "applicable": self.applicable,
            "detail": self.detail,
        }


@dataclass
class EntityScore:
    entity_id: str
    team: str
    total: float
    terms: list[ScoreTerm]
    metrics: EntityMetrics

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "team": self.team,
            "total": round(self.total, 2),
            "terms": [t.to_dict() for t in self.terms],
            "metrics": self.metrics.to_dict(),
        }


@dataclass
class RunScore:
    run_id: str
    scenario: str
    seed: int
    entities: list[EntityScore]
    teams: dict[str, float]
    weights_hash: str
    max_points: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "scenario": self.scenario,
            "seed": self.seed,
            "max_points": round(self.max_points, 2),
            "weights_hash": self.weights_hash,
            "entities": [e.to_dict() for e in self.entities],
            "teams": {team: round(value, 2) for team, value in self.teams.items()},
        }


class ScoreEngine:
    """Turns measurements into weighted, explainable scores."""

    def __init__(self, settings: ScoringSettings | None = None) -> None:
        self.settings = settings or ScoringSettings()

    @property
    def weights(self) -> ScoreWeights:
        return self.settings.weights

    @property
    def thresholds(self) -> ScoreThresholds:
        return self.settings.thresholds

    @property
    def weights_hash(self) -> str:
        """Identifies the standard a score was produced under.

        Two scores are only comparable when this matches: change a weight or a
        threshold and every previous score was measured against a different
        ruler.
        """
        payload = json.dumps(
            {
                "weights": self.weights.model_dump(),
                "thresholds": self.thresholds.model_dump(),
                "redistribute": self.settings.redistribute_inapplicable,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]

    # ------------------------------------------------------------------ API

    def score_recording(self, recording: Recording) -> RunScore:
        """Score every entity in a recording."""
        metrics = compute_metrics(
            recording, formation_settle_fraction=self.thresholds.formation_settle_fraction
        )
        scores = [self.score_entity(m) for m in metrics.values()]
        scores.sort(key=lambda s: s.total, reverse=True)

        teams: dict[str, list[float]] = {}
        for score in scores:
            teams.setdefault(score.team or "UNKNOWN", []).append(score.total)

        return RunScore(
            run_id=recording.run_id,
            scenario=recording.scenario,
            seed=recording.seed,
            entities=scores,
            teams={team: sum(v) / len(v) for team, v in teams.items()},
            weights_hash=self.weights_hash,
            max_points=self.weights.total(),
        )

    def score_entity(self, metrics: EntityMetrics) -> EntityScore:
        """Score one unit, redistributing any term that does not apply to it."""
        terms = [
            self._survival(metrics),
            self._navigation(metrics),
            self._formation(metrics),
            self._safety(metrics),
            self._efficiency(metrics),
            self._information(metrics),
        ]

        if self.settings.redistribute_inapplicable:
            terms = self._redistribute(terms)

        for term in terms:
            term.points = term.fraction * term.weight

        return EntityScore(
            entity_id=metrics.entity_id,
            team=metrics.team,
            total=sum(t.points for t in terms),
            terms=terms,
            metrics=metrics,
        )

    def _redistribute(self, terms: list[ScoreTerm]) -> list[ScoreTerm]:
        """Spread an inapplicable term's weight over the terms that do apply.

        Without this, a unit with no leader is silently punished for a term it
        could never have earned. The breakdown still shows the term, marked
        inapplicable with zero weight, so the redistribution is visible rather
        than hidden in a total.
        """
        spare = sum(t.weight for t in terms if not t.applicable)
        if spare <= 0:
            return terms

        applicable = [t for t in terms if t.applicable]
        base = sum(t.weight for t in applicable)
        if base <= 0:
            return terms

        for term in terms:
            if term.applicable:
                term.weight += spare * (term.weight / base)
            else:
                term.weight = 0.0
        return terms

    # ---------------------------------------------------------------- terms

    def _survival(self, m: EntityMetrics) -> ScoreTerm:
        """Stayed in the run and finished it in one piece."""
        # Being active throughout is most of it; finishing active is the rest.
        fraction = 0.7 * m.active_fraction + (0.3 if m.ended_active else 0.0)
        return ScoreTerm(
            name="survival",
            fraction=_clamp01(fraction),
            weight=self.weights.survival,
            points=0.0,
            detail={
                "active_fraction": round(m.active_fraction, 4),
                "final_status": m.final_status,
                "ended_active": m.ended_active,
            },
        )

    def _navigation(self, m: EntityMetrics) -> ScoreTerm:
        """Held the commanded course and altitude, and got round the route.

        Route progress is judged against what the run had time for. demo_alpha's
        first leg is 40 km — three minutes at cruise — so a two-minute run that
        reaches no waypoint has not failed at navigation, and scoring it as
        though it had would make the term meaningless. When the run was too
        short to expect even one waypoint, progress drops out and the course and
        altitude components are renormalised to carry the term on their own.
        """
        t = self.thresholds
        heading = _decay(m.mean_abs_heading_error_deg, t.heading_tolerance_deg)
        altitude = _decay(m.mean_abs_altitude_error_m, t.altitude_tolerance_m)

        expected = m.run_duration_s / t.seconds_per_expected_waypoint
        # Two ways progress cannot be judged: the unit was never given a route
        # (a wingman holds station, it does not navigate), or the run was too
        # short for even one waypoint to be expected.
        progress_applies = m.had_route and expected >= 1.0
        progress = _clamp01(m.waypoints_reached / expected) if progress_applies else 0.0

        if progress_applies:
            fraction = 0.45 * heading + 0.30 * altitude + 0.25 * progress
        else:
            # 0.45 : 0.30 renormalised to sum to one.
            fraction = 0.6 * heading + 0.4 * altitude

        applicable = m.decisions > 0
        return ScoreTerm(
            name="navigation",
            fraction=_clamp01(fraction) if applicable else 0.0,
            weight=self.weights.navigation,
            points=0.0,
            applicable=applicable,
            detail={
                "mean_abs_heading_error_deg": round(m.mean_abs_heading_error_deg, 2),
                "heading_tolerance_deg": t.heading_tolerance_deg,
                "heading_fraction": round(heading, 4),
                "mean_abs_altitude_error_m": round(m.mean_abs_altitude_error_m, 2),
                "altitude_tolerance_m": t.altitude_tolerance_m,
                "altitude_fraction": round(altitude, 4),
                "had_route": m.had_route,
                "waypoints_reached": m.waypoints_reached,
                "waypoints_expected": round(expected, 2),
                "progress_scored": progress_applies,
                "progress_fraction": round(progress, 4) if progress_applies else None,
                "reason": None if applicable else "no decisions recorded for this unit",
                "progress_note": self._progress_note(m, expected, progress_applies),
            },
        )

    def _progress_note(self, m: EntityMetrics, expected: float, scored: bool) -> str | None:
        """Say why route progress was left out, when it was."""
        if scored:
            return None
        if not m.had_route:
            return "this unit was never assigned a route, so route progress was not scored"
        return (
            f"run lasted {m.run_duration_s:.0f}s; less than the "
            f"{self.thresholds.seconds_per_expected_waypoint:.0f}s a waypoint is expected to "
            "take, so route progress was not scored"
        )

    def _formation(self, m: EntityMetrics) -> ScoreTerm:
        """Held station on its leader — only meaningful if it had one."""
        t = self.thresholds
        if not m.had_leader:
            return ScoreTerm(
                name="formation",
                fraction=0.0,
                weight=self.weights.formation,
                points=0.0,
                applicable=False,
                detail={"reason": "unit was never assigned a leader"},
            )

        if m.formation_samples == 0:
            # Assigned a leader but never able to measure station error: the
            # leader was never seen. That is a real failure, not a missing term.
            return ScoreTerm(
                name="formation",
                fraction=0.0,
                weight=self.weights.formation,
                points=0.0,
                detail={"reason": "leader was assigned but never observed"},
            )

        # Time in station is the honest measure — a unit that sits on station
        # and one that oscillates through it can share a mean. The median error
        # then separates "just inside tolerance" from "welded on".
        settled = m.station_errors_settled
        in_station = (
            sum(1 for e in settled if e <= t.formation_tolerance_m) / len(settled) if settled else 0.0
        )
        closeness = _decay(m.median_station_error_settled_m, t.formation_tolerance_m)
        fraction = _clamp01(0.7 * in_station + 0.3 * closeness)

        return ScoreTerm(
            name="formation",
            fraction=fraction,
            weight=self.weights.formation,
            points=0.0,
            detail={
                "in_station_fraction": round(in_station, 4),
                "median_station_error_m": round(m.median_station_error_settled_m, 2),
                "mean_station_error_settled_m": round(m.mean_station_error_settled_m, 2),
                "mean_station_error_whole_run_m": round(m.mean_station_error_m, 2),
                "formation_tolerance_m": t.formation_tolerance_m,
                "settle_fraction": t.formation_settle_fraction,
                "samples_scored": len(settled),
                "samples_total": m.formation_samples,
                "note": (
                    "scored on the settled tail of the run; the join-up from the spawn position is excluded"
                ),
            },
        )

    def _safety(self, m: EntityMetrics) -> ScoreTerm:
        """Flew inside its limits without the safety layer having to save it.

        Three different things, kept apart because they mean different things:

        * a **rejected** command is a genuine failure — the controller refused
          to fly what the agent asked for;
        * an **envelope intervention** is the protection easing a demand back.
          That is the safety layer working, not a fault, and it fires on every
          control tick an aggressive manoeuvre lasts — so counting them would
          measure how long the turn was. It is scored as a fraction of the run;
        * **leaving the world** is its own category.

        Conflating these scored a clean waypoint turn as a total safety
        failure, because 1.5 seconds of banked climb produced 45 clamp events.
        """
        t = self.thresholds
        rejection = _decay(float(m.rejected_commands), float(t.rejected_commands_for_zero))
        intervention = _decay(m.envelope_intervention_fraction, t.envelope_intervention_fraction_for_zero)
        boundary = _decay(
            float(m.out_of_bounds_events + m.collision_events), float(t.boundary_events_for_zero)
        )
        fraction = _clamp01(0.4 * rejection + 0.3 * intervention + 0.3 * boundary)

        return ScoreTerm(
            name="safety",
            fraction=fraction,
            weight=self.weights.safety,
            points=0.0,
            detail={
                "rejected_commands": m.rejected_commands,
                "rejected_commands_for_zero": t.rejected_commands_for_zero,
                "rejection_fraction": round(rejection, 4),
                "envelope_interventions": m.envelope_interventions,
                "envelope_intervention_fraction": round(m.envelope_intervention_fraction, 5),
                "envelope_fraction_for_zero": t.envelope_intervention_fraction_for_zero,
                "intervention_fraction": round(intervention, 4),
                "out_of_bounds_events": m.out_of_bounds_events,
                "collision_events": m.collision_events,
                "boundary_events_for_zero": t.boundary_events_for_zero,
                "boundary_fraction": round(boundary, 4),
            },
        )

    def _efficiency(self, m: EntityMetrics) -> ScoreTerm:
        """Flew smoothly rather than thrashing the controls."""
        t = self.thresholds
        fraction = _decay(m.mean_control_rate_per_s, t.control_rate_for_zero)
        return ScoreTerm(
            name="efficiency",
            fraction=fraction,
            weight=self.weights.efficiency,
            points=0.0,
            detail={
                "mean_control_rate_per_s": round(m.mean_control_rate_per_s, 4),
                "max_control_rate_per_s": round(m.max_control_rate_per_s, 4),
                "rate_for_zero": t.control_rate_for_zero,
            },
        )

    def _information(self, m: EntityMetrics) -> ScoreTerm:
        """Quality of the picture the unit actually held.

        Confidence is what the unit reported about its own observation, and
        track age is how stale that picture was. Both come from the recorded
        decisions, so this measures the picture it *had*, not the truth.
        """
        t = self.thresholds
        applicable = m.decisions > 0
        freshness = _decay(m.mean_track_age_s, t.track_age_tolerance_s)
        fraction = 0.6 * _clamp01(m.mean_observation_confidence) + 0.4 * freshness
        return ScoreTerm(
            name="information",
            fraction=_clamp01(fraction) if applicable else 0.0,
            weight=self.weights.information,
            points=0.0,
            applicable=applicable,
            detail={
                "mean_observation_confidence": round(m.mean_observation_confidence, 4),
                "mean_track_age_s": round(m.mean_track_age_s, 3),
                "track_age_tolerance_s": t.track_age_tolerance_s,
                "freshness_fraction": round(freshness, 4),
                "mean_contacts": round(m.mean_contacts, 2),
                "reason": None if applicable else "no decisions recorded for this unit",
            },
        )
