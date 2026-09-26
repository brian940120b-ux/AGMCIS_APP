"""The extended observation (COMP PHASE 16).

Everything here is derived from the 26 doubles 表 1 already sends — no extra
information from anywhere, which is what keeps it inside the rules. What it
costs is compatibility: a policy with thirty inputs cannot be the organiser's,
and theirs cannot be ours. That is why it is a choice per session and why the
reference encoding is left exactly as it was.
"""

from __future__ import annotations

import numpy as np
import pytest

from competition.features import (
    EXTENDED_STATE_SIZE,
    EXTRA_FIELDS,
    EXTRA_SIZE,
    ExtendedEncoder,
    specific_energy_m,
)
from competition.state import STATE_SIZE, StateEncoder, Telemetry


def flying(**overrides) -> Telemetry:
    values = dict.fromkeys(Telemetry.__dataclass_fields__, 0.0)
    values.update(
        own_lat_deg=23.0,
        own_lon_deg=121.0,
        own_alt_ft=15_000.0,
        own_vt_fps=574.0,
        own_vc_fps=574.0,
        enemy_lat_deg=23.02,
        enemy_lon_deg=121.0,
        enemy_alt_ft=15_000.0,
        enemy_vn_fps=574.0,
    )
    values.update(overrides)
    return Telemetry(**values)


def test_the_reference_twenty_are_untouched_underneath():
    """The whole point of adding rather than replacing: a reference policy has
    to keep loading, and the differential test against the organiser's own
    function has to keep passing."""
    telemetry = flying()
    assert np.allclose(
        ExtendedEncoder().encode(telemetry)[:STATE_SIZE],
        StateEncoder().encode(telemetry),
    )


def test_the_shape_is_the_reference_plus_the_named_extras():
    assert EXTENDED_STATE_SIZE == STATE_SIZE + EXTRA_SIZE
    assert len(EXTRA_FIELDS) == EXTRA_SIZE
    assert ExtendedEncoder().encode(flying()).shape == (EXTENDED_STATE_SIZE,)


def index(name: str) -> int:
    return STATE_SIZE + EXTRA_FIELDS.index(name)


# ------------------------------------------------------------------ the G


def test_our_own_g_is_in_there_because_the_scoring_charges_for_it():
    """The gap this module exists for. 2000 a second for tracking, 1000 a
    second above 9G, and the reference state contains neither the G nor
    anything it could be computed from."""
    state = ExtendedEncoder().encode(flying(own_g_acc=-9.0))
    assert state[index("own_g_load")] == pytest.approx(-1.0), "the limit reads as one"


def test_the_g_sign_is_the_published_one_not_the_intuitive_one():
    """Measured in this environment: level flight -0.40, a hard pull -4.24, a
    pushover +3.75. Body Z points down, so a positive-G pull is negative here.

    Pinned because the elevator's sign was assumed once and cost a working
    safety floor, and this is the same kind of trap one field along.
    """
    pull = ExtendedEncoder().encode(flying(own_g_acc=-4.24))[index("own_g_load")]
    push = ExtendedEncoder().encode(flying(own_g_acc=+3.75))[index("own_g_load")]
    assert pull < 0 < push


def test_the_scorings_abs_catches_both_directions():
    """Which is why the scoring is right to use abs() and this is right not to."""
    from competition.scoring import G_LIMIT

    assert abs(-9.5) > G_LIMIT, "a 9.5G pull"
    assert abs(+9.5) > G_LIMIT, "and a -9.5G pushover"


# -------------------------------------------------------------- the others


def test_specific_energy_is_height_plus_what_the_speed_could_buy():
    assert specific_energy_m(1000.0, 0.0) == pytest.approx(1000.0)
    # 100 m/s is worth about 510 m of climb.
    assert specific_energy_m(0.0, 100.0) == pytest.approx(100**2 / (2 * 9.80665))


def test_the_energy_advantage_is_signed_the_way_it_reads():
    higher = ExtendedEncoder().encode(flying(own_alt_ft=20_000.0, enemy_alt_ft=10_000.0))
    lower = ExtendedEncoder().encode(flying(own_alt_ft=10_000.0, enemy_alt_ft=20_000.0))
    assert higher[index("energy_advantage")] > 0 > lower[index("energy_advantage")]


def test_the_opponents_speed_survives_at_all():
    """Their velocity arrives in fields 24-26 and the reference encoding uses
    it only for an angle, throwing the magnitude away."""
    fast = ExtendedEncoder().encode(flying(enemy_vn_fps=1000.0))
    slow = ExtendedEncoder().encode(flying(enemy_vn_fps=200.0))
    assert fast[index("enemy_speed")] > slow[index("enemy_speed")]


def test_rates_are_zero_on_the_first_frame_of_a_round():
    """Not a difference against whatever the last round ended on."""
    state = ExtendedEncoder().encode(flying())
    for name in ("azimuth_rate", "elevation_rate", "aspect_rate", "enemy_turn_rate"):
        assert state[index(name)] == 0.0, name


def test_a_rate_is_reported_once_there_are_two_frames():
    encoder = ExtendedEncoder()
    encoder.encode(flying(enemy_lat_deg=23.02))
    moved = encoder.encode(flying(enemy_lat_deg=23.02, enemy_lon_deg=121.01))
    assert moved[index("azimuth_rate")] != 0.0


def test_a_bearing_crossing_the_tail_is_not_a_twenty_thousand_degree_turn():
    """Azimuth runs -180 to 180, so the wrap is a 360-degree step in one frame
    and 360 degrees at 60 Hz is 21,600 a second."""
    encoder = ExtendedEncoder()
    encoder.encode(flying(enemy_lat_deg=22.98, enemy_lon_deg=121.0001))
    crossed = encoder.encode(flying(enemy_lat_deg=22.98, enemy_lon_deg=120.9999))
    assert abs(crossed[index("azimuth_rate")]) < 10.0


def test_resetting_forgets_the_previous_round():
    encoder = ExtendedEncoder()
    for _ in range(5):
        encoder.encode(flying(enemy_lon_deg=121.01))
    encoder.reset()
    state = encoder.encode(flying())
    assert state[index("azimuth_rate")] == 0.0
    assert state[index("round_elapsed")] == pytest.approx(1 / 60 / 300)


def test_the_clock_runs_and_stops_at_the_end_of_the_round():
    encoder = ExtendedEncoder(round_seconds=1.0)
    for _ in range(120):  # two seconds of a one-second round
        state = encoder.encode(flying())
    assert state[index("round_elapsed")] == pytest.approx(1.0), "clamped, not past the end"
