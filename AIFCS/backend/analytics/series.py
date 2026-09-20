"""Chart series, computed from what a run actually stored (PHASE 17).

The aggregation belongs here rather than in the browser. A run holds thousands
of telemetry samples and tens of thousands of decisions; shipping those to a
dashboard so it can group them would make every chart a download, and two
clients grouping the same rows differently would disagree about the same run.
So the backend answers in the shape a chart consumes: named series of points,
one matrix, one set of shares.

Nothing here reads the live world. Analysis is downstream of truth, exactly as
the scoring engine is — these functions take rows that a finished run wrote and
cannot reach a tick. Changing a chart cannot change how an aircraft flies.

**A run with nothing stored says so.** Recording can be switched off in
``configs/analysis.yaml``, and runs from before a table existed simply have no
rows. Returning empty series would draw a flat line at zero, which is a claim
about the run rather than an absence of data, so every group carries whether it
has anything and why not.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

# One point is [x, y]. Compact on the wire, and the axis label says what x is.
Point = list[float]


@dataclass
class Series:
    """One line or one set of bars, named for its legend entry."""

    key: str
    label: str
    points: list[Point] = field(default_factory=list)
    # Which colour family the series belongs to. The frontend maps a group to a
    # validated palette; it never invents a colour per series.
    group: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "label": self.label, "group": self.group, "points": self.points}


@dataclass
class Chart:
    """A set of series that share one pair of axes."""

    key: str
    title: str
    x_label: str
    y_label: str
    unit: str
    series: list[Series] = field(default_factory=list)
    # Empty is a fact about the run, not a chart with nothing in it.
    available: bool = True
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "x_label": self.x_label,
            "y_label": self.y_label,
            "unit": self.unit,
            "available": self.available,
            "detail": self.detail,
            "series": [s.to_dict() for s in self.series],
        }


def _empty(key: str, title: str, detail: str) -> Chart:
    return Chart(key=key, title=title, x_label="", y_label="", unit="", available=False, detail=detail)


def _round(value: float, places: int = 2) -> float:
    return round(float(value), places)


# --------------------------------------------------------------- time series


def flight_charts(samples: list[dict[str, Any]]) -> list[Chart]:
    """Altitude and speed against simulation time, one series per unit.

    Coloured by team rather than by unit: eight units would need eight
    categorical hues, which is past the point where a reader can tell them
    apart. Two team hues plus a direct label on each line carries the identity
    without asking colour to do work it cannot do.
    """
    if not samples:
        detail = "This run stored no telemetry samples — recording may have been disabled."
        return [
            _empty("altitude", "Altitude", detail),
            _empty("speed", "Speed", detail),
        ]

    by_entity: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sample in samples:
        by_entity[str(sample["entity_id"])].append(sample)

    altitude = Chart(
        key="altitude", title="Altitude", x_label="simulation time", y_label="altitude", unit="m"
    )
    speed = Chart(key="speed", title="Speed", x_label="simulation time", y_label="speed", unit="m/s")

    for entity_id in sorted(by_entity):
        rows = sorted(by_entity[entity_id], key=lambda r: r["simulation_time"])
        group = _team_of(entity_id)
        altitude.series.append(
            Series(
                key=entity_id,
                label=entity_id,
                group=group,
                points=[[_round(r["simulation_time"], 3), _round(r["altitude_m"], 1)] for r in rows],
            )
        )
        speed.series.append(
            Series(
                key=entity_id,
                label=entity_id,
                group=group,
                points=[[_round(r["simulation_time"], 3), _round(r["speed_mps"], 1)] for r in rows],
            )
        )
    return [altitude, speed]


def survival_chart(samples: list[dict[str, Any]]) -> Chart:
    """How many units each team still had flying, against time.

    Counted from the sampled status rather than from the final state, so a unit
    lost halfway through shows where it was lost.
    """
    if not samples:
        return _empty(
            "survival",
            "Units flying",
            "This run stored no telemetry samples — recording may have been disabled.",
        )

    # time -> team -> how many were ACTIVE at that sample
    counts: dict[float, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    teams: set[str] = set()
    for sample in samples:
        team = _team_of(str(sample["entity_id"]))
        teams.add(team)
        at = _round(sample["simulation_time"], 3)
        counts[at]  # touch, so a time with nothing active still exists
        if str(sample.get("status", "")).upper() == "ACTIVE":
            counts[at][team] += 1

    chart = Chart(
        key="survival",
        title="Units flying",
        x_label="simulation time",
        y_label="units active",
        unit="",
    )
    for team in sorted(teams):
        chart.series.append(
            Series(
                key=team,
                label=team,
                group=team,
                points=[[at, float(counts[at].get(team, 0))] for at in sorted(counts)],
            )
        )
    return chart


def coordination_chart(decisions: list[dict[str, Any]], bucket_s: float = 5.0) -> Chart:
    """How much of each team was flying formation, against time.

    Read from what the agents decided, not from geometry: a unit is coordinating
    when its own decision says so. Bucketed, because decisions arrive at 10 Hz
    per unit and a point per decision would be noise rather than a trend.
    """
    if not decisions:
        return _empty(
            "coordination",
            "Units in formation",
            "This run stored no decisions — it may have ended before any agent decided.",
        )

    # bucket -> team -> set of entities that decided FORMATION in it
    formation: dict[float, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    buckets: set[float] = set()
    teams: set[str] = set()
    for decision in decisions:
        at = float(decision["simulation_time"])
        bucket = _round(at - (at % bucket_s), 3)
        buckets.add(bucket)
        team = _team_of(str(decision["entity_id"]))
        teams.add(team)
        if str(decision.get("behaviour", "")).upper() == "FORMATION":
            formation[bucket][team].add(str(decision["entity_id"]))

    chart = Chart(
        key="coordination",
        title="Units in formation",
        x_label="simulation time",
        y_label="units",
        unit="",
    )
    for team in sorted(teams):
        chart.series.append(
            Series(
                key=team,
                label=team,
                group=team,
                points=[[b, float(len(formation[b].get(team, ())))] for b in sorted(buckets)],
            )
        )
    return chart


# ------------------------------------------------------------- score matrix


@dataclass
class ScoreMatrix:
    """Every scored unit against every scoring term.

    A grid of magnitudes, which is why it is drawn as a heatmap on one hue
    rather than as six stacked colours: six categorical hues cannot be told
    apart reliably, and the reader's question here is "where did this unit lose
    points", which is magnitude, not identity.
    """

    entities: list[str] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)
    # entity -> term -> {"fraction": 0..1, "points": earned, "weight": available}
    cells: dict[str, dict[str, dict[str, float]]] = field(default_factory=dict)
    totals: dict[str, float] = field(default_factory=dict)
    available_points: float = 0.0
    available: bool = True
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "entities": self.entities,
            "terms": self.terms,
            "cells": self.cells,
            "totals": self.totals,
            "available_points": self.available_points,
            "available": self.available,
            "detail": self.detail,
        }


def score_matrix(scores: list[dict[str, Any]]) -> ScoreMatrix:
    """Build the entity-by-term grid from stored scores."""
    entity_scores = [s for s in scores if s.get("subject") == "entity"]
    if not entity_scores:
        return ScoreMatrix(
            available=False,
            detail=(
                "This run has no per-unit scores — scoring may have been disabled, or the run is still going."
            ),
        )

    matrix = ScoreMatrix()
    term_order: list[str] = []
    for score in sorted(entity_scores, key=lambda s: str(s["subject_id"])):
        entity_id = str(score["subject_id"])
        breakdown = score.get("breakdown") or {}
        terms = breakdown.get("terms") or []
        if not terms:
            continue

        matrix.entities.append(entity_id)
        matrix.totals[entity_id] = _round(score.get("total", 0.0))
        row: dict[str, dict[str, float]] = {}
        for term in terms:
            name = str(term.get("name", ""))
            if name not in term_order:
                term_order.append(name)
            row[name] = {
                "fraction": _round(term.get("fraction", 0.0), 4),
                "points": _round(term.get("points", 0.0)),
                "weight": _round(term.get("weight", 0.0)),
                # A term that did not apply is not a zero score — a unit with no
                # route cannot be marked down for navigation it was never given.
                "applicable": 1.0 if term.get("applicable", True) else 0.0,
            }
        matrix.cells[entity_id] = row

    if not matrix.entities:
        return ScoreMatrix(
            available=False,
            detail=(
                "Scores were stored for this run, but without the per-term breakdown that a heatmap needs."
            ),
        )

    matrix.terms = term_order
    first = matrix.cells[matrix.entities[0]]
    matrix.available_points = _round(sum(cell["weight"] for cell in first.values()))
    return matrix


# ---------------------------------------------------------- behaviour shares


@dataclass
class BehaviourShares:
    """What fraction of its decisions each unit spent in each behaviour."""

    entities: list[str] = field(default_factory=list)
    behaviours: list[str] = field(default_factory=list)
    shares: dict[str, dict[str, float]] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    available: bool = True
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "entities": self.entities,
            "behaviours": self.behaviours,
            "shares": self.shares,
            "counts": self.counts,
            "available": self.available,
            "detail": self.detail,
        }


# Stacking order. Validated as a categorical palette in this order, so a chart
# must stack it in this order for the adjacent-pair separation to hold.
BEHAVIOUR_ORDER = ["HOLD", "NAVIGATE", "PATROL", "FORMATION", "AVOID"]


def behaviour_shares(decisions: list[dict[str, Any]]) -> BehaviourShares:
    """How each unit spent its decisions, as a share of its own total."""
    if not decisions:
        return BehaviourShares(
            available=False,
            detail="This run stored no decisions — it may have ended before any agent decided.",
        )

    tally: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    seen: list[str] = []
    for decision in decisions:
        entity_id = str(decision["entity_id"])
        behaviour = str(decision.get("behaviour", "")).upper() or "UNKNOWN"
        tally[entity_id][behaviour] += 1
        if behaviour not in seen:
            seen.append(behaviour)

    ordered = [b for b in BEHAVIOUR_ORDER if b in seen]
    ordered += [b for b in seen if b not in BEHAVIOUR_ORDER]
    result = BehaviourShares(entities=sorted(tally), behaviours=ordered)
    for entity_id in result.entities:
        total = sum(tally[entity_id].values())
        result.counts[entity_id] = total
        result.shares[entity_id] = {
            behaviour: _round(tally[entity_id].get(behaviour, 0) / total, 4) for behaviour in ordered
        }
    return result


# ------------------------------------------------------------------ helpers


def _team_of(entity_id: str) -> str:
    """The team an entity belongs to, from its id.

    Ids are ``TEAM-NN`` by convention across every scenario. Falling back to the
    whole id keeps an unconventional id in its own group rather than silently
    folding it into someone else's team.
    """
    return entity_id.split("-", 1)[0] if "-" in entity_id else entity_id
