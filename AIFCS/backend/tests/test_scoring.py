"""Scoring tests (PHASE 9).

Several of these pin behaviour that was wrong first time round and was only
found by running a real scenario and reading the numbers. They are here so it
cannot regress quietly.
"""

from __future__ import annotations

import pytest

from core.config import ScoreThresholds, ScoreWeights, ScoringSettings
from core.simulation_engine import SimulationEngine
from replay.reader import load_recording
from replay.recorder import ReplayRecorder
from scoring.engine import ScoreEngine
from scoring.metrics import EntityMetrics, compute_metrics


@pytest.fixture(scope="module")
def recording(tmp_path_factory):
    """One 300-second run of the reference scenario, shared by these tests."""
    directory = tmp_path_factory.mktemp("scoring")
    engine = SimulationEngine()
    engine.load_scenario("demo_alpha")
    recorder = ReplayRecorder(engine, directory=directory, record_rate_hz=20.0)
    engine.tick_observers.append(recorder.capture)
    recorder.start()
    engine.step(60 * 300)
    summary = recorder.stop("duration reached")
    return load_recording(summary["path"])


@pytest.fixture
def scorer():
    return ScoreEngine(ScoringSettings())


# ------------------------------------------------------------------ metrics


def test_metrics_measure_every_unit(recording):
    metrics = compute_metrics(recording)
    assert set(metrics) == {"BLUE-01", "BLUE-02", "RED-01", "RED-02"}

    lead = metrics["BLUE-01"]
    assert lead.frames > 100
    assert lead.distance_travelled_m > 10_000
    assert lead.decisions > 100
    assert lead.run_duration_s == pytest.approx(recording.duration_s)


def test_only_wingmen_are_recorded_as_having_a_leader(recording):
    metrics = compute_metrics(recording)
    assert metrics["BLUE-02"].had_leader is True
    assert metrics["RED-02"].had_leader is True
    assert metrics["BLUE-01"].had_leader is False
    assert metrics["RED-01"].had_leader is False


def test_only_leaders_are_recorded_as_having_a_route(recording):
    """A wingman holds station; it is never given waypoints."""
    metrics = compute_metrics(recording)
    assert metrics["BLUE-01"].had_route is True
    assert metrics["BLUE-02"].had_route is False


def test_envelope_clamps_are_not_counted_as_rejections(recording):
    """ACTION_REJECTED covers two different things.

    A command the controller refused is a failure. The envelope protection
    easing a demand back is the safety layer working, and it fires on every
    control tick an aggressive manoeuvre lasts. Counting them together scored a
    clean waypoint turn as a total safety failure.
    """
    metrics = compute_metrics(recording)
    lead = metrics["BLUE-01"]
    assert lead.envelope_interventions > 0, "the reference run turns hard enough to clamp"
    assert lead.rejected_commands == 0
    assert lead.safety_events == 0
    # Interventions are a small share of a long run, not a per-tick tally.
    assert 0.0 < lead.envelope_intervention_fraction < 0.05


def test_settled_station_error_excludes_the_join_up(recording):
    """A wingman spawned away from station has to fly there first."""
    wingman = compute_metrics(recording)["BLUE-02"]
    assert wingman.formation_samples > 100
    assert wingman.mean_station_error_settled_m < wingman.mean_station_error_m


# -------------------------------------------------------------------- terms


def test_a_clean_run_scores_well(recording, scorer):
    result = scorer.score_recording(recording)
    assert result.max_points == pytest.approx(100.0)
    for entity in result.entities:
        assert 50.0 < entity.total <= 100.0, f"{entity.entity_id} scored {entity.total}"


def test_leaders_are_not_scored_on_formation(recording, scorer):
    result = scorer.score_recording(recording)
    lead = next(e for e in result.entities if e.entity_id == "BLUE-01")
    formation = next(t for t in lead.terms if t.name == "formation")

    assert formation.applicable is False
    assert formation.weight == 0.0
    assert formation.points == 0.0
    assert "never assigned a leader" in formation.detail["reason"]


def test_an_inapplicable_term_is_redistributed_not_forfeited(recording, scorer):
    """A unit must not lose points for an order it was never given."""
    result = scorer.score_recording(recording)
    lead = next(e for e in result.entities if e.entity_id == "BLUE-01")

    assert sum(t.weight for t in lead.terms) == pytest.approx(100.0)
    survival = next(t for t in lead.terms if t.name == "survival")
    assert survival.weight > scorer.weights.survival


def test_a_wingman_is_not_scored_on_route_progress(recording, scorer):
    result = scorer.score_recording(recording)
    wingman = next(e for e in result.entities if e.entity_id == "BLUE-02")
    navigation = next(t for t in wingman.terms if t.name == "navigation")

    assert navigation.detail["progress_scored"] is False
    assert "never assigned a route" in navigation.detail["progress_note"]


def test_route_progress_is_skipped_when_the_run_was_too_short(scorer):
    """demo_alpha's first leg is three minutes; a short run has not failed."""
    metrics = EntityMetrics(entity_id="X", run_duration_s=30.0, decisions=10, had_route=True)
    term = scorer.score_entity(metrics).terms[1]

    assert term.name == "navigation"
    assert term.detail["progress_scored"] is False
    assert "too short" in term.detail["progress_note"] or "less than" in term.detail["progress_note"]


def test_formation_rewards_time_in_station(scorer):
    """Two units with the same mean can differ; time in station separates them."""
    steady = EntityMetrics(
        entity_id="steady",
        had_leader=True,
        formation_samples=4,
        station_errors_settled=[100.0, 100.0, 100.0, 100.0],
        median_station_error_settled_m=100.0,
    )
    swinging = EntityMetrics(
        entity_id="swinging",
        had_leader=True,
        formation_samples=4,
        station_errors_settled=[0.0, 400.0, 0.0, 400.0],
        median_station_error_settled_m=200.0,
    )

    steady_term = next(t for t in scorer.score_entity(steady).terms if t.name == "formation")
    swinging_term = next(t for t in scorer.score_entity(swinging).terms if t.name == "formation")
    assert steady_term.fraction > swinging_term.fraction


def test_a_unit_that_never_saw_its_leader_is_marked_failed_not_excused(scorer):
    metrics = EntityMetrics(entity_id="lost", had_leader=True, formation_samples=0)
    term = next(t for t in scorer.score_entity(metrics).terms if t.name == "formation")

    assert term.applicable is True
    assert term.fraction == 0.0
    assert "never observed" in term.detail["reason"]


def test_rejected_commands_cost_far_more_than_clamps(scorer):
    clamped = EntityMetrics(entity_id="a", envelope_interventions=30, control_ticks=18_000)
    rejected = EntityMetrics(entity_id="b", rejected_commands=30, control_ticks=18_000)

    clamped_term = next(t for t in scorer.score_entity(clamped).terms if t.name == "safety")
    rejected_term = next(t for t in scorer.score_entity(rejected).terms if t.name == "safety")
    assert clamped_term.fraction > 0.9
    assert rejected_term.fraction < 0.7


def test_every_term_reports_the_measurement_behind_it(recording, scorer):
    """A score nobody can explain is not worth having."""
    result = scorer.score_recording(recording)
    for entity in result.entities:
        for term in entity.terms:
            assert term.detail, f"{entity.entity_id}/{term.name} has no explanation"


def test_scoring_is_reproducible(recording, scorer):
    first = scorer.score_recording(recording).to_dict()
    second = scorer.score_recording(recording).to_dict()
    assert first == second


# ------------------------------------------------------------------ the ruler


def test_the_weights_hash_changes_when_the_standard_does():
    base = ScoreEngine(ScoringSettings())
    heavier = ScoreEngine(ScoringSettings(weights=ScoreWeights(survival=99.0)))
    stricter = ScoreEngine(ScoringSettings(thresholds=ScoreThresholds(heading_tolerance_deg=10.0)))

    assert base.weights_hash != heavier.weights_hash
    assert base.weights_hash != stricter.weights_hash
    assert base.weights_hash == ScoreEngine(ScoringSettings()).weights_hash


def test_scoring_has_no_weapon_or_targeting_term():
    """AIFCS models none, and scoring one would imply capability it must not have."""
    from scoring.engine import TERM_NAMES

    forbidden = ("weapon", "target", "engagement", "kill", "hit", "damage", "strike")
    assert not any(word in name.lower() for name in TERM_NAMES for word in forbidden)
    assert set(TERM_NAMES) == set(ScoreWeights().model_dump())
