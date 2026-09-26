"""Limiting the elevator on measured G instead of on speed (COMP PHASE 15).

The sample client caps the elevator at 0.4 above Mach 0.8 and calls it a G
limiter. It is not one: it uses speed as a stand-in for load factor and pays
for the guess twice over. Measured in a maximum-rate turn —

                    19,000 ft / 500 kt   3,000 ft / 500 kt   3,000 ft / 350 kt
    mach 0.4            6.2 deg/s            8.8 deg/s          16.4 deg/s
    G limit, margin 2  12.1                 15.7                16.4

— it halves the turn rate where it binds and does nothing at all where the
aircraft actually reaches high G. Load factor is in the OBS packet the host
sends (`own_g_acc`), so there is nothing to guess at.

表 2 gives the elevator channel as -1~+1 and 公告說明 五.3.(2).C says the client
may be rewritten, so both limits are ours to choose between.
"""

from __future__ import annotations

import numpy as np
import pytest

from competition.action import (
    ELEVATOR_LIMIT_HIGH_SPEED,
    G_LIMIT_MARGIN,
    JoystickState,
    g_limited,
    shape_command,
)

SUPERSONIC = 0.95


def settle(mach: float, **kwargs) -> float:
    """Where the elevator ends up after the rate limiter has had its say."""
    stick = JoystickState()
    value = 0.0
    for _ in range(120):
        value = float(shape_command(np.array([0.0, -1.0, 0.0, 0.8]), stick, mach, **kwargs)[1])
    return value


# ------------------------------------------------------------- the sign of it


def test_pulling_makes_negative_g_and_the_limiter_knows_it():
    """The bug that made the first version do nothing at all.

    In this model a nose-up elevator of -1.0 sits alongside `n-pilot-z-norm` of
    -9.34, so "this input is loading the aircraft" is the two having the *same*
    sign. The first version tested for opposite signs, concluded a full pull at
    -9.3G was unloading, and passed -1.000 through for 2.9 seconds while its own
    headroom read 0.00.
    """
    assert g_limited(-1.0, -9.3, 9.0, 2.0) == pytest.approx(0.0), "pulling at the limit"
    assert g_limited(1.0, 9.3, 9.0, 2.0) == pytest.approx(0.0), "pushing at the limit"


def test_recovering_from_high_g_keeps_full_authority():
    """Taking the stick away from a policy unloading the aircraft is the worst
    moment to do it, so the cutback is one-directional."""
    assert g_limited(1.0, -9.3, 9.0, 2.0) == 1.0, "pushing out of a hard pull"
    assert g_limited(-1.0, 9.3, 9.0, 2.0) == -1.0, "pulling out of a hard push"


def test_it_is_out_of_the_way_until_the_margin():
    floor = 9.0 - G_LIMIT_MARGIN
    assert g_limited(-1.0, 0.0, 9.0, G_LIMIT_MARGIN) == -1.0
    assert g_limited(-1.0, -floor, 9.0, G_LIMIT_MARGIN) == pytest.approx(-1.0)
    assert g_limited(-1.0, -(floor + 1.0), 9.0, G_LIMIT_MARGIN) == pytest.approx(-0.5)


# ------------------------------------------------------ what the shaping sends


def test_the_speed_limit_is_what_costs_the_turn_rate():
    """Above Mach 0.8 the sample gives 0.4 of elevator however hard you pull."""
    assert settle(SUPERSONIC) == pytest.approx(-ELEVATOR_LIMIT_HIGH_SPEED, abs=1e-6)


def test_on_g_a_fast_aircraft_that_is_not_loaded_gets_everything():
    """The case the speed limit gets wrong: Mach 0.95 and 1G is not a G problem,
    and it is most of a diving reversal."""
    assert settle(SUPERSONIC, g_load=-1.0, g_limit=9.0) == pytest.approx(-1.0)


def test_on_g_a_loaded_aircraft_is_cut_back_whatever_its_speed():
    """And the case it gets wrong in the other direction: slow and at the limit
    is a G problem, and the speed limit never fires there at all."""
    subsonic_loaded = settle(0.5, g_load=-9.0, g_limit=9.0)
    assert subsonic_loaded == pytest.approx(0.0, abs=1e-6)
    assert settle(0.5) == pytest.approx(-1.0), "without it, nothing stops the pull"


def test_leaving_it_unset_reproduces_the_sample_exactly():
    """Every session trained before this existed learned the sample's plant."""
    for mach in (0.5, SUPERSONIC):
        assert settle(mach, g_load=-9.0) == settle(mach), "g_load alone changes nothing"
        assert settle(mach, g_limit=9.0) == settle(mach), "g_limit alone changes nothing"
