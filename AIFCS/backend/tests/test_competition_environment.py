"""The competition environment (COMP PHASE 4).

JSBSim is optional, so these skip without it rather than failing — the same
rule the rest of the platform follows.
"""

from __future__ import annotations

import re

import numpy as np
import pytest

from competition.environment import (
    CompetitionRound,
    EnvConfig,
    RoundSetup,
    action_space,
    level_opponent,
    observation_space,
    resolve_jsbsim_root,
)
from competition.state import STATE_SIZE

pytest.importorskip("jsbsim")

LEVEL = np.array([0.0, 0.0, 0.0, 0.8])


@pytest.fixture
def round_() -> CompetitionRound:
    return CompetitionRound(EnvConfig(), seed=20261107)


# ------------------------------------------------------------ round setup


def test_the_default_setup_is_the_published_one():
    """競賽規則 1: 3,000 / 6,000 / 9,000 ft, 10,000-20,000 ft, 340 knots."""
    setup = RoundSetup()
    assert setup.separations_ft == (3000.0, 6000.0, 9000.0)
    assert setup.altitude_range_ft == (10_000.0, 20_000.0)
    assert setup.speed_kcas == 340.0


def test_the_reference_setup_is_available_and_is_not_the_same():
    """The reference trains on merges and deck heights the rules never produce."""
    reference = RoundSetup.reference()
    assert min(reference.separations_ft) == 0.0
    assert max(reference.separations_ft) == 12_000.0
    assert reference.altitude_range_ft == (1_000.0, 22_000.0)


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_a_round_starts_inside_the_published_envelope(seed: int):
    started = CompetitionRound(EnvConfig(), seed=seed)
    geometry = started.geometry()
    assert 2900.0 <= geometry.distance_ft <= 9200.0, geometry.distance_ft
    assert 9_900.0 <= geometry.own_alt_m / 0.3048 <= 20_100.0


def test_two_rounds_with_the_same_seed_are_the_same_round():
    a = CompetitionRound(EnvConfig(), seed=99)
    b = CompetitionRound(EnvConfig(), seed=99)
    np.testing.assert_allclose(a.observe(), b.observe())
    for _ in range(120):
        first = a.step(LEVEL)[0]
        second = b.step(LEVEL)[0]
    np.testing.assert_allclose(first, second)


# ------------------------------------------------------------------ spaces


def test_the_observation_space_matches_the_reference_bounds():
    """So a policy trained against the reference loads here unchanged."""
    space = observation_space()
    assert space.shape == (STATE_SIZE,)
    assert space.low.min() == -500.0
    assert space.high.max() == 500.0


def test_the_rudder_is_pinned_shut_by_default_as_the_reference_pins_it():
    pinned = action_space(rudder_enabled=False)
    assert pinned.high[2] == pytest.approx(1e-17)
    opened = action_space(rudder_enabled=True)
    assert opened.high[2] == 1.0
    assert opened.low[2] == -1.0


# -------------------------------------------------------------- the world


def test_the_state_the_env_produces_is_the_state_the_client_would(round_):
    """Both go through the one encoder, so this is a guard, not a hope."""
    import struct

    from competition.protocol import decode_observation
    from competition.state import StateEncoder, Telemetry

    telemetry = round_.telemetry()
    wire = struct.pack("<26d", *telemetry.as_observation())
    from_wire = StateEncoder().encode(Telemetry.from_observation(decode_observation(wire)))
    np.testing.assert_array_equal(round_.observe(), from_wire)


def test_the_opponent_holds_its_altitude(round_):
    """It goes straight to the surfaces, because the elevator curve is a cube.

    Routed through the stick shaping instead, a hold-altitude loop's small
    corrections come out a thousandth of their size and the aircraft flies into
    the ground. It did, at 21 seconds, before this was noticed.
    """
    started_ft = round_.foe.altitude_m / 0.3048
    for _ in range(1800):  # 30 s
        _, _, done, _ = round_.step(LEVEL)
        if done:
            break
    assert abs(round_.foe.altitude_m / 0.3048 - started_ft) < 300.0


def test_an_untrimmed_aircraft_with_no_stick_eventually_hits_the_ground(round_):
    """run_ic sets a state, it does not trim one. The reference behaves the same.

    Worth a test because it is the environment's only intrinsic pressure: a
    policy that does nothing loses, which is what makes the deck penalty and the
    crash termination do any work.
    """
    frame = 0
    for frame in range(18_000):  # noqa: B007 - the count is the assertion
        _, _, done, reason = round_.step(LEVEL)
        if done:
            break
    assert reason == "CRASH"
    assert frame < 60 * 120, "it should not take two minutes to fall out of the sky"


def test_the_score_runs_alongside_the_round(round_):
    for _ in range(600):
        _, _geometry, done, _ = round_.step(np.array([0.1, -0.05, 0.0, 0.9]))
        if done:
            break
    assert round_.score.elapsed_s > 0
    assert round_.score.as_dict()["total"] is not None


# ------------------------------------------------------------- jsbsim root


def test_a_missing_data_root_is_refused_with_the_path_in_the_message(tmp_path):
    with pytest.raises(FileNotFoundError, match=re.escape("aircraft/f16/f16.xml")):
        resolve_jsbsim_root(str(tmp_path))


def test_no_root_means_the_installed_package():
    assert resolve_jsbsim_root(None) is None


def test_the_config_describes_itself_for_a_model_card():
    described = EnvConfig().describe()
    assert described["separations_ft"] == [3000.0, 6000.0, 9000.0]
    assert described["jsbsim_root"] == "installed package"
    assert described["rudder_enabled"] is False
    assert described["attack_range_ft"] == [500.0, 3000.0]


def test_the_opponent_is_a_plain_callable_returning_four_channels():
    command = level_opponent(15_000.0)(CompetitionRound(EnvConfig(), seed=5).telemetry())
    assert command.shape == (4,)
    assert np.all(np.isfinite(command))


# --------------------------------------------- initial conditions, in order


def test_setting_the_speed_before_the_altitude_leaves_the_aircraft_slow():
    """The reference sets ic/vc-kts first, and pays 106 knots for it.

    A calibrated airspeed set before the altitude is resolved at sea level; the
    later altitude change preserves true airspeed instead, and the aircraft
    ends up well below what was asked for. It is also why the Mach 0.8 elevator
    limit never fires in the reference environment — nothing there gets close.
    """
    asked_kcas = RoundSetup().speed_kcas

    correct = CompetitionRound(EnvConfig(speed_before_altitude=False), seed=3)
    reference = CompetitionRound(EnvConfig(speed_before_altitude=True), seed=3)

    def kcas(round_: CompetitionRound) -> float:
        return float(round_.own.fdm["velocities/vc-fps"]) / 1.68781

    assert kcas(correct) == pytest.approx(asked_kcas, rel=0.01)
    assert kcas(reference) < asked_kcas - 80.0
    assert float(correct.own.fdm["velocities/mach"]) > float(reference.own.fdm["velocities/mach"])


def test_the_correct_ordering_is_the_default():
    """The published rules say 340 knots, so 340 knots is what we fly."""
    assert EnvConfig().speed_before_altitude is False


def test_the_ordering_choice_reaches_the_model_card():
    described = EnvConfig(speed_before_altitude=True).describe()
    assert described["speed_before_altitude"] is True


# ------------------------------------------------------------- the opponent


def test_the_reference_opponent_holds_the_speed_it_is_given():
    """The whole reason the environment was not validated for a day.

    A fixed-throttle opponent accelerates away: 563 knots against a target of
    340 after two and a half minutes. A pursuer 223 knots slower never closes,
    and the reference policy went from 9 kills in 12 rounds to 1 — which read
    like a broken environment and was a target drone with the throttle open.
    """
    round_ = CompetitionRound(EnvConfig(opponent="reference"), seed=3)
    for _ in range(9000):  # 150 s
        _, _, done, _ = round_.step(LEVEL)
        if done:
            break
    held_kcas = float(round_.foe.fdm["velocities/vc-fps"]) / 1.68781
    assert held_kcas == pytest.approx(RoundSetup().speed_kcas, abs=25.0)


def test_a_fixed_throttle_opponent_runs_away_which_is_why_it_is_not_the_default():
    round_ = CompetitionRound(EnvConfig(opponent="level"), seed=3)
    for _ in range(9000):
        _, _, done, _ = round_.step(LEVEL)
        if done:
            break
    ran_to_kcas = float(round_.foe.fdm["velocities/vc-fps"]) / 1.68781
    assert ran_to_kcas > RoundSetup().speed_kcas + 150.0


def test_the_faithful_opponent_is_the_default():
    assert EnvConfig().opponent == "reference"
    assert EnvConfig().describe()["opponent"] == "reference"


def test_the_reference_opponent_never_manoeuvres():
    """Its heading logic picks from ["Straight"] — the turns are commented out.

    Worth pinning, because "the opponent turns" was one of the explanations
    tried for the missing kills, and it was wrong: the source says otherwise.
    """
    round_ = CompetitionRound(EnvConfig(opponent="reference"), seed=11)
    headings = []
    for frame in range(3600):
        _, _, done, _ = round_.step(LEVEL)
        if frame % 600 == 0:
            headings.append(float(round_.foe.fdm["attitude/psi-deg"]))
        if done:
            break
    assert max(headings) - min(headings) < 20.0, headings
