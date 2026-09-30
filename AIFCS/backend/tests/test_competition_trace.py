"""Recording a round frame by frame, and reading it back.

Everything known about a policy until now was a mean over twenty rounds. That
found the problem — v6 reaches the cone and does not stay — and cannot describe
it: 0.38 seconds could be one pass or six, and those want different fixes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from competition.scoring import AttackEnvelope
from competition.trace import RoundTrace, passes, read_trace, summarise


class _Geometry:
    """Just the fields a trace records."""

    def __init__(self, track_angle_deg: float, distance_m: float = 300.0) -> None:
        self.track_angle_deg = track_angle_deg
        self.distance_m = distance_m
        self.distance_ft = distance_m / 0.3048
        self.aspect_angle_deg = 0.0
        self.own_alt_m = 3000.0
        self.enemy_alt_m = 3000.0


def _write(tmp_path: Path, angles: list[float]) -> Path:
    path = tmp_path / "round.jsonl.gz"
    trace = RoundTrace(path, label="t", opponent="break", seed=7, envelope=AttackEnvelope())
    trace.open()
    accumulated = 0.0
    for angle in angles:
        geometry = _Geometry(angle)
        if AttackEnvelope().contains(geometry):
            accumulated += 1 / 60.0
        trace.record(geometry, attack_seconds=accumulated, g_load=1.0, floor_active=False)
    trace.close(outcome={"killed": False}, reason="TIME")
    return path


def test_a_round_survives_the_round_trip(tmp_path: Path):
    path = _write(tmp_path, [40.0, 0.5, 0.5, 40.0])
    header, frames, end = read_trace(path)

    assert header["opponent"] == "break"
    assert header["envelope"]["kill_seconds"] == 3.0, "a trace has to say which rules scored it"
    assert [round(f["track_angle_deg"], 1) for f in frames] == [40.0, 0.5, 0.5, 40.0]
    assert end["end_reason"] == "TIME"
    assert end["frames"] == 4


def test_the_envelope_flag_is_recorded_not_recomputed(tmp_path: Path):
    """So a trace read later cannot disagree with the run that produced it."""
    path = _write(tmp_path, [40.0, 0.5])
    _, frames, _ = read_trace(path)

    assert frames[0]["in_envelope"] is False
    assert frames[1]["in_envelope"] is True


def test_one_long_pass_and_six_short_ones_are_told_apart(tmp_path: Path):
    """The whole point. Both of these total the same time in the cone."""
    long_one = _write(tmp_path / "a", [40.0] + [0.5] * 12 + [40.0])
    short_ones = _write(tmp_path / "b", ([0.5] * 2 + [40.0] * 2) * 6)

    assert len(passes(read_trace(long_one)[1])) == 1
    assert len(passes(read_trace(short_ones)[1])) == 6


def test_a_pass_that_runs_to_the_end_of_the_round_still_counts(tmp_path: Path):
    """An off-by-one here would silently drop the most interesting pass there
    is — the one the round ended during."""
    path = _write(tmp_path, [40.0, 0.5, 0.5])

    found = passes(read_trace(path)[1])
    assert len(found) == 1
    # Rounded to three places on the way out, deliberately — a round is
    # eighteen thousand frames and the digits cost more than they say.
    assert found[0]["seconds"] == pytest.approx(2 / 60.0, abs=1e-3)


def test_a_pass_reports_how_close_it_came(tmp_path: Path):
    path = _write(tmp_path, [0.9, 0.2, 0.7])

    assert passes(read_trace(path)[1])[0]["closest_deg"] == pytest.approx(0.2)


def test_the_summary_says_what_is_missing(tmp_path: Path):
    """A number you can read today beats a chart you can read next week."""
    path = _write(tmp_path, [40.0] + [0.5] * 60 + [40.0])

    text = summarise(path)
    assert "1 pass(es)" in text
    assert "3.00 s needed" in text
    # One second held, three needed: it has to say the gap, not just the total.
    assert "2.00 s longer" in text


def test_a_round_that_never_got_in_says_how_close_it_came(tmp_path: Path):
    path = _write(tmp_path, [40.0, 12.0, 40.0])

    text = summarise(path)
    assert "never got in" in text
    assert "12.0 degrees" in text


# ------------------------------------------------------------- the page


def test_the_page_puts_the_cone_on_the_chart_even_when_nothing_goes_near_it():
    """The first render fitted the aim axis to the data, so a round whose nose
    never got closer than 91 degrees dropped the one-degree line off the bottom
    — the chart hid the only thing it exists to show. The domain is fixed now,
    which also makes two rounds comparable at a glance."""
    from competition.trace_html import _PAGE

    assert "lo: 0.2, hi: 180" in _PAGE
    assert "ticks: [0.2, 1, 10, 100]" in _PAGE


def test_the_page_carries_no_network_calls(tmp_path: Path):
    """It has to open from a file:// path on a laptop with nothing running."""
    from competition.trace_html import render

    page = render(_write(tmp_path, [40.0, 0.5, 0.5]))

    # The SVG namespace is a constant identifier passed to createElementNS, not
    # an address anything is fetched from. Removed before the check rather than
    # dropped from the list, so a real http:// still fails it.
    body = page.replace("http://www.w3.org/2000/svg", "")

    for forbidden in ("http://", "https://", "<script src", "<link ", "@import", "fetch("):
        assert forbidden not in body, f"the page reaches out: {forbidden}"


def test_downsampling_keeps_the_closest_approach(tmp_path: Path):
    """A pass through the cone lasts about 0.6 s — 36 frames. Sampling every
    Nth frame would drop most passes and flatten the rest, which is the one
    thing this page must not do."""
    from competition.trace_html import _downsample

    frames = [
        {
            "t": i / 60.0,
            "track_angle_deg": 0.4 if i == 50 else 40.0,
            "distance_m": 300.0,
            "in_envelope": i == 50,
            "g_load": 1.0,
        }
        for i in range(600)
    ]

    points = _downsample(frames, buckets=10)
    assert min(p["angle"] for p in points) == pytest.approx(0.4), "the closest frame survived"
    assert any(p["inside"] for p in points), "and so did the fact that it was in the cone"


def test_a_round_with_no_passes_does_not_read_as_a_near_miss(tmp_path: Path):
    """ "3.00 s short on its best pass" for a round with no passes at all is
    true and reads as though there was nearly one."""
    from competition.trace_html import render

    page = render(_write(tmp_path, [40.0, 40.0]))

    assert "Never inside the cone" in page
    assert "short of a kill on its best pass" not in page
