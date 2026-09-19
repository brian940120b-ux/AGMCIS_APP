"""Reward engine tests (PHASE 12).

Several of these pin behaviour that was wrong first time round and was only
found by training against it and reading the term breakdown.
"""

from __future__ import annotations

import numpy as np
import pytest

from agents.base_agent import ContactView, Observation
from core.config import RewardWeights
from core.world_state import EntityStatus, Team
from simulation.aircraft import ControlInputs
from training.reward import TERM_NAMES, RewardConfig, RewardEngine


def _entity(altitude=6000.0, throttle=0.26, status=EntityStatus.ACTIVE, position=None):
    from core.world_state import EntityState

    return EntityState(
        id="BLUE-01",
        team=Team.BLUE,
        position=np.array(position if position is not None else [0.0, 0.0, altitude]),
        velocity=np.array([220.0, 0.0, 0.0]),
        controls=ControlInputs(throttle=throttle),
        status=status,
    )


def _observation(contacts=None, confidence=1.0):
    return Observation(
        agent_id="A",
        entity_id="BLUE-01",
        team=Team.BLUE,
        simulation_time=0.0,
        position=np.zeros(3),
        velocity=np.array([220.0, 0.0, 0.0]),
        orientation=np.zeros(3),
        angular_velocity=np.zeros(3),
        altitude=6000.0,
        speed=220.0,
        heading_deg=90.0,
        contacts=contacts or [],
        confidence=confidence,
    )


def _contact(distance=5000.0, friendly=True, age=0.0, confidence=1.0, entity_id="BLUE-02"):
    return ContactView(
        entity_id=entity_id,
        team=Team.BLUE if friendly else Team.RED,
        relative_position=np.array([distance, 0.0, 0.0]),
        relative_velocity=np.zeros(3),
        distance_m=distance,
        is_friendly=friendly,
        age_s=age,
        confidence=confidence,
    )


def _compute(engine, **overrides):
    kwargs = {
        "entity": _entity(),
        "observation": _observation(),
        "goal_position": None,
        "leader_offset_error_m": None,
        "team_size": 1,
        "terminated_reason": None,
    }
    kwargs.update(overrides)
    return engine.compute(**kwargs)


@pytest.fixture
def engine():
    return RewardEngine(RewardWeights(), RewardConfig())


# --------------------------------------------------------------------- scope


def test_there_is_no_weapon_or_targeting_term():
    """AIFCS models none, and rewarding one would train a behaviour it must not have."""
    forbidden = ("weapon", "target", "engage", "kill", "hit", "strike", "damage", "destroy")
    assert not any(word in name.lower() for name in TERM_NAMES for word in forbidden)


def test_every_term_has_a_weight():
    """A typo in a weight name must fail loudly, not score zero."""
    assert set(TERM_NAMES) == set(RewardWeights().model_dump())


# --------------------------------------------------------------------- terms


def test_the_breakdown_explains_the_total(engine):
    breakdown = _compute(engine)
    assert set(breakdown.terms) == set(TERM_NAMES)
    assert breakdown.total == pytest.approx(sum(breakdown.weighted.values()))


def test_crash_is_terminal_and_survival_is_per_step(engine):
    """These were one term, and rolling them together made a constant that
    paid every step regardless of what the policy did."""
    alive = _compute(engine)
    assert alive.terms["survival"] == 1.0
    assert alive.terms["crash_penalty"] == 0.0

    crashed = _compute(engine, terminated_reason="ground_impact")
    assert crashed.terms["crash_penalty"] == 1.0
    assert crashed.weighted["crash_penalty"] < -1.0
    assert crashed.total < 0.0


@pytest.mark.parametrize("reason", ["out_of_bounds", "disabled", "ground_impact", "removed"])
def test_every_way_of_losing_the_aircraft_is_penalised(engine, reason):
    assert _compute(engine, terminated_reason=reason).terms["crash_penalty"] == 1.0


def test_an_inactive_entity_counts_as_lost_even_without_a_reason(engine):
    breakdown = _compute(engine, entity=_entity(status=EntityStatus.DISABLED))
    assert breakdown.terms["crash_penalty"] == 1.0
    assert breakdown.terms["survival"] == 0.0


def test_flying_outside_the_altitude_band_earns_no_survival(engine):
    assert _compute(engine, entity=_entity(altitude=50.0)).terms["survival"] == 0.0
    assert _compute(engine, entity=_entity(altitude=6000.0)).terms["survival"] == 1.0


def test_navigation_pays_for_progress_not_for_proximity(engine):
    """Rewarding closeness would pay a policy to sit still next to the goal."""
    goal = np.array([1000.0, 0.0, 6000.0])

    # First step has nothing to compare against.
    assert (
        _compute(engine, entity=_entity(position=[0, 0, 6000]), goal_position=goal).terms["navigation"] == 0.0
    )
    # Closing the distance pays.
    closer = _compute(engine, entity=_entity(position=[100, 0, 6000]), goal_position=goal)
    assert closer.terms["navigation"] > 0
    # Standing still pays nothing, even though it is close.
    still = _compute(engine, entity=_entity(position=[100, 0, 6000]), goal_position=goal)
    assert still.terms["navigation"] == pytest.approx(0.0)
    # Going backwards costs.
    away = _compute(engine, entity=_entity(position=[0, 0, 6000]), goal_position=goal)
    assert away.terms["navigation"] < 0


def test_mission_pays_once_for_arriving(engine):
    goal = np.array([0.0, 0.0, 6000.0])
    assert (
        _compute(engine, entity=_entity(position=[10_000, 0, 6000]), goal_position=goal).terms["mission"]
        == 0.0
    )
    assert _compute(engine, entity=_entity(position=[0, 0, 6000]), goal_position=goal).terms["mission"] == 1.0
    assert engine.goals_reached == 1


def test_formation_does_not_apply_without_a_leader(engine):
    """A unit must not be punished for an order it never received."""
    assert _compute(engine, leader_offset_error_m=None).terms["formation"] == 0.0
    assert "no leader" in _compute(engine, leader_offset_error_m=None).notes["formation"]


def test_formation_decays_with_station_error(engine):
    tight = _compute(engine, leader_offset_error_m=10.0).terms["formation"]
    loose = _compute(engine, leader_offset_error_m=250.0).terms["formation"]
    gone = _compute(engine, leader_offset_error_m=5000.0).terms["formation"]
    assert tight > loose > gone
    assert gone == 0.0


def test_coordination_measures_awareness_not_proximity(engine):
    """A formation that has lost track of half itself is not coordinated."""
    blind = _compute(engine, observation=_observation(), team_size=3)
    assert blind.terms["coordination"] == 0.0

    half = _compute(engine, observation=_observation([_contact(entity_id="BLUE-02")]), team_size=3)
    assert half.terms["coordination"] == pytest.approx(0.5)

    full = _compute(
        engine,
        observation=_observation([_contact(entity_id="BLUE-02"), _contact(entity_id="BLUE-03")]),
        team_size=3,
    )
    assert full.terms["coordination"] == pytest.approx(1.0)


def test_coordination_is_not_scored_when_there_is_no_team(engine):
    assert _compute(engine, team_size=1).terms["coordination"] == 0.0


def test_information_rewards_a_fresh_confident_picture(engine):
    fresh = _compute(engine, observation=_observation([_contact(age=0.0, confidence=1.0)]))
    stale = _compute(engine, observation=_observation([_contact(age=3.0, confidence=1.0)]))
    unsure = _compute(engine, observation=_observation([_contact(age=0.0)], confidence=0.2))
    assert fresh.terms["information"] > stale.terms["information"]
    assert fresh.terms["information"] > unsure.terms["information"]


def test_the_collision_penalty_grows_as_separation_closes(engine):
    clear = _compute(engine, observation=_observation([_contact(distance=5000.0)]))
    near = _compute(engine, observation=_observation([_contact(distance=150.0)]))
    touching = _compute(engine, observation=_observation([_contact(distance=1.0)]))

    assert clear.terms["collision_penalty"] == 0.0
    assert 0.0 < near.terms["collision_penalty"] < touching.terms["collision_penalty"]
    assert near.weighted["collision_penalty"] < 0.0


def test_smoothness_is_measured_on_the_applied_controls(engine):
    """Otherwise a policy could demand something violent and let the rate
    limiter tidy it up while still collecting the smoothness reward."""
    _compute(engine, entity=_entity(throttle=0.0))
    steady = _compute(engine, entity=_entity(throttle=0.0))
    assert steady.terms["control_smoothness"] == 1.0

    _compute(engine, entity=_entity(throttle=0.0))
    thrashed = _compute(engine, entity=_entity(throttle=1.0))
    assert thrashed.terms["control_smoothness"] < 1.0


def test_reset_clears_the_per_episode_memory(engine):
    goal = np.array([1000.0, 0.0, 6000.0])
    _compute(engine, entity=_entity(position=[0, 0, 6000]), goal_position=goal)
    _compute(engine, entity=_entity(position=[0, 0, 6000]), goal_position=goal)
    assert engine.goals_reached == 0

    engine.reset()
    # After a reset the first step again has nothing to compare against.
    first = _compute(engine, entity=_entity(position=[500, 0, 6000]), goal_position=goal)
    assert first.terms["navigation"] == 0.0


def test_the_learnable_terms_outweigh_the_constant_floor():
    """The defect that stopped the first policy learning.

    Survival, coordination, information and smoothness are near-constant for a
    competent policy. If their combined per-step weight approaches navigation's,
    the signal a policy can act on is swamped by one it cannot.
    """
    weights = RewardWeights()
    floor = weights.survival + weights.coordination + weights.information + weights.control_smoothness
    assert floor < weights.navigation, (
        f"constant per-step terms total {floor} against navigation {weights.navigation}; "
        "a policy cannot learn from that"
    )
