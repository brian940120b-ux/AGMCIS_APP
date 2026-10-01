"""The turn-performance measurement: the plant we fly, measured, not quoted."""

from __future__ import annotations

import math

import pytest

pytest.importorskip("jsbsim")

from competition.turntable import SPEEDS_KCAS, measure, table


def test_a_pull_turns_the_flight_path_and_the_numbers_are_physical():
    point = measure(400.0, altitude_ft=15_000.0, seconds=2.0)

    assert 5.0 < point.peak_rate_deg_s < 30.0
    assert 1.5 < point.peak_g <= 9.0, "the 9-G limiter is on"
    assert 1_500.0 < point.radius_ft_at_peak < 8_000.0
    assert math.isfinite(point.kcas_after)
    # rate = 1092 * G_radial / KTAS is the handbook's own identity; at 15,000 ft
    # KTAS is about 1.25 x KCAS. Loosely, so the plant and not the arithmetic is
    # what is being checked.
    g_radial = math.sqrt(max(point.peak_g**2 - 1.0, 0.0))
    implied = 1092.0 * g_radial / (point.kcas_at_peak * 1.25)
    assert abs(implied - point.peak_rate_deg_s) < 0.5 * point.peak_rate_deg_s


def test_the_table_has_a_row_per_speed():
    points = [measure(k, altitude_ft=15_000.0, seconds=0.5) for k in SPEEDS_KCAS[:2]]
    text = table(points)
    assert text.count("\n") == 1 + len(points)
