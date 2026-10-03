"""Training-only initial geometries (AIFCS 2.0 hypothesis H6; SRC-012 trained on
abreast : head-on = 4 : 1, SRC-006 on generated intermediate-difficulty starts).

The evaluator never reads these from a card, so nothing here can change what a
policy is scored on; that is asserted below rather than assumed.
"""

from __future__ import annotations

import random

import numpy as np
import pytest

from competition.environment import (
    GEOMETRIES,
    MIXTURES,
    RANGED_GEOMETRIES,
    CompetitionRound,
    EnvConfig,
    RoundSetup,
    initial_geometry,
    resolve_geometry,
)

HOLD = np.array([0.0, 0.0, 0.0, 0.8])


def start(geometry: str, seed: int):
    return CompetitionRound(EnvConfig(setup=RoundSetup(geometry=geometry)), seed=seed).geometry()


def test_the_published_start_draws_the_same_three_numbers_it_always_did():
    a, b = random.Random(5), random.Random(5)
    assert initial_geometry(a, "published") == (b.uniform(0, 360), b.uniform(0, 360), b.uniform(0, 360))


def test_the_default_setup_is_the_published_one():
    assert RoundSetup().geometry == "published"
    assert GEOMETRIES[0] == "published"


@pytest.mark.parametrize("seed", range(3))
def test_head_on_puts_both_noses_on_each_other(seed: int):
    g = start("headon", seed)
    assert abs(g.azimuth_deg) < 3.0
    assert g.aspect_angle_deg < 3.0, "the target is pointing at us"


@pytest.mark.parametrize("seed", range(3))
def test_abreast_is_the_three_nine_line_flying_apart(seed: int):
    g = start("abreast", seed)
    assert 87.0 < abs(g.azimuth_deg) < 93.0
    assert 87.0 < g.aspect_angle_deg < 93.0


@pytest.mark.parametrize("seed", range(3))
def test_offensive_starts_behind_a_target_flying_away(seed: int):
    g = start("offensive", seed)
    assert abs(g.azimuth_deg) <= 23.0
    assert g.aspect_angle_deg >= 157.0, "its tail is towards us"


@pytest.mark.parametrize("seed", range(3))
def test_defensive_starts_with_the_target_on_our_tail(seed: int):
    g = start("defensive", seed)
    assert abs(g.azimuth_deg) >= 157.0
    assert g.aspect_angle_deg <= 23.0, "it is pointing at us"


def test_the_mix_is_four_abreast_to_one_head_on():
    rng = random.Random(11)
    abreast = 0
    for _ in range(2000):
        bearing, own, foe = initial_geometry(rng, "mix")
        assert (
            abs(((foe - own + 180.0) % 360.0) - 180.0) == pytest.approx(180.0, abs=1e-9)
            or abs(((foe - own) % 360.0) - 180.0) < 1e-9
        )
        if abs((((bearing - own) % 360.0) + 180.0) % 360.0 - 180.0) > 45.0:
            abreast += 1
    assert 0.77 < abreast / 2000 < 0.83


def test_separation_altitude_and_speed_stay_the_rules_own_under_every_geometry():
    for geometry in GEOMETRIES:
        setup = RoundSetup(geometry=geometry)
        assert setup.separations_ft == (3000.0, 6000.0, 9000.0)
        assert setup.altitude_range_ft == (10_000.0, 20_000.0)
        assert setup.speed_kcas == 340.0
        g = start(geometry, 1)
        if geometry in RANGED_GEOMETRIES:
            low, high = RANGED_GEOMETRIES[geometry]
            assert low - 50.0 <= g.distance_ft <= high + 50.0, geometry
        else:
            assert 2900.0 <= g.distance_ft <= 9200.0, geometry


@pytest.mark.parametrize("seed", range(3))
def test_wez_starts_inside_the_cone_on_a_target_flying_away(seed: int):
    g = start("wez", seed)
    assert 500.0 < g.distance_ft < 3000.0
    assert g.track_angle_deg < 1.0, "inside the half-angle from the first frame"
    assert g.aspect_angle_deg > 165.0, "its tail is towards us"


@pytest.mark.parametrize("seed", range(3))
def test_wezdef_starts_with_the_target_in_our_six_pointing_at_us(seed: int):
    g = start("wezdef", seed)
    assert 500.0 < g.distance_ft < 3000.0
    assert abs(g.azimuth_deg) > 178.0
    assert g.aspect_angle_deg < 15.0, "it is pointing at us"


@pytest.mark.parametrize("seed", range(3))
def test_cz_starts_in_the_control_zone(seed: int):
    """AETC TTP 11-1: 2,500-4,500 ft slant range, 25-45 degrees off the tail."""
    g = start("cz", seed)
    assert 2450.0 <= g.distance_ft <= 4550.0
    # 25-45 degrees of heading off its tail, plus up to 10 degrees of our own
    # bearing jitter, in the reference's 180-is-behind numbers.
    assert 125.0 <= g.aspect_angle_deg <= 168.0
    assert abs(g.azimuth_deg) < 15.0


def test_an_unknown_geometry_is_refused():
    with pytest.raises(ValueError, match="geometry must be one of"):
        initial_geometry(random.Random(0), "spiral")


def test_the_card_says_which_geometry_and_the_evaluator_ignores_it():
    from competition.evaluate import config_from_card

    described = EnvConfig(setup=RoundSetup(geometry="offensive")).describe()
    assert described["geometry"] == "offensive"
    rebuilt = config_from_card({"environment": described}, "reference", 1.0)
    assert rebuilt.setup.geometry == "published", "the exam is the published start, whatever the training was"


def test_the_trainer_offers_it_inherits_it_and_defaults_to_published():
    from competition import train

    args = train.parse_args(["--name", "x"])
    assert args.geometry is None
    train.apply_defaults(args)
    assert args.geometry == "published"
    assert train.INHERITED["geometry"] == ("environment", "geometry")
    assert train.parse_args(["--name", "x", "--geometry", "mix"]).geometry == "mix"


def test_a_geometry_change_on_resume_is_a_curriculum_stage_not_a_refusal(tmp_path, capsys):
    from competition import train
    from competition.session import Session, SessionState

    session = Session(tmp_path / "s")
    old = SessionState(
        name="s", algorithm="sac", timesteps_done=500_000, environment={"geometry": "offensive"}
    )
    new = SessionState(name="s", algorithm="sac", environment={"geometry": "published"})
    session.check_compatible(old, new)  # no IncompatibleSession
    train.refresh_growable(old, new)
    assert old.environment["geometry"] == "published"
    assert old.environment["geometry_history"] == [
        {"from_step": 0, "geometry": "offensive"},
        {"from_step": 500_000, "geometry": "published"},
    ]
    assert "curriculum stage" in capsys.readouterr().out
    # The history itself must not block the next resume.
    session.check_compatible(
        old, SessionState(name="s", algorithm="sac", environment={"geometry": "published"})
    )


def test_a_round_flies_from_each_geometry():
    for geometry in GEOMETRIES:
        round_ = CompetitionRound(EnvConfig(setup=RoundSetup(geometry=geometry)), seed=2)
        for _ in range(5):
            state, _, _, _ = round_.step(HOLD)
        assert np.all(np.isfinite(state))


def test_defmix_is_half_published_and_a_quarter_each_of_the_two_defensive_starts():
    rng = random.Random(11)
    draws = [resolve_geometry(rng, "defmix") for _ in range(4000)]
    assert abs(draws.count("published") / 4000 - 0.5) < 0.04
    assert abs(draws.count("defensive") / 4000 - 0.25) < 0.04
    assert abs(draws.count("wezdef") / 4000 - 0.25) < 0.04
    assert set(MIXTURES) == {"defmix"}
    assert resolve_geometry(rng, "published") == "published", "a concrete name passes through"


def test_a_defmix_round_sets_the_range_from_the_geometry_it_drew():
    """When the draw is wezdef the range is the envelope's; otherwise the rules'."""
    seen = {"inside": 0, "rules": 0}
    for seed in range(12):
        g = start("defmix", seed)
        if g.distance_ft < 2900.0:
            seen["inside"] += 1
            assert 500.0 < g.distance_ft < 3000.0
            assert abs(g.azimuth_deg) > 178.0, "wezdef: on our tail"
        else:
            seen["rules"] += 1
            assert 2900.0 <= g.distance_ft <= 9200.0
    assert seen["inside"] and seen["rules"], seen


def test_swapping_seats_flies_the_same_engagement_from_the_other_chair():
    """Same seed, other chair: we start where they would have, on their
    heading, and they start where we would have."""
    from competition.environment import CompetitionRound, EnvConfig

    normal = CompetitionRound(config=EnvConfig(), seed=5)
    normal.reset(seed=5)
    swapped = CompetitionRound(config=EnvConfig(swap_seats=True), seed=5)
    swapped.reset(seed=5)

    a, b = normal.telemetry(), swapped.telemetry()
    assert b.own_lat_deg == pytest.approx(a.enemy_lat_deg, abs=1e-6)
    assert b.own_lon_deg == pytest.approx(a.enemy_lon_deg, abs=1e-6)
    assert b.enemy_lat_deg == pytest.approx(a.own_lat_deg, abs=1e-6)
    assert b.enemy_lon_deg == pytest.approx(a.own_lon_deg, abs=1e-6)
    assert b.own_yaw_deg == pytest.approx(normal.foe.telemetry_against(normal.own).own_yaw_deg, abs=0.01)
    assert b.own_yaw_deg != pytest.approx(a.own_yaw_deg, abs=1.0)


def test_the_same_policy_on_both_seats_scores_the_same_round_from_either_chair():
    """With one policy on both chairs, the swapped round is the same fight
    with the names exchanged, so our score there is their score here. This
    is the check that the chair itself is worth nothing — which it was not,
    twice, before the evaluator held decisions and the pool opponent encoded
    every frame."""
    from competition.environment import CompetitionRound, EnvConfig
    from competition.evaluate import neutral_policy, play_round
    from competition.league import PolicyOpponent

    stick = neutral_policy()
    foe = PolicyOpponent(lambda observation: stick(observation))
    config = EnvConfig(round_seconds=3.0, opponent_policy=foe)
    normal = play_round(stick, config, seed=9)
    swapped = play_round(stick, EnvConfig(round_seconds=3.0, opponent_policy=foe, swap_seats=True), seed=9)
    assert normal.frames == swapped.frames == 180
    assert swapped.outcome.blue["advantage_score"] == pytest.approx(
        normal.outcome.red["advantage_score"], rel=0.02, abs=1.0
    )
    assert swapped.outcome.red["advantage_score"] == pytest.approx(
        normal.outcome.blue["advantage_score"], rel=0.02, abs=1.0
    )
    assert CompetitionRound  # imported for the reader: the swap lives in its reset
