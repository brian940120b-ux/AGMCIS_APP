"""The host's own CSV, read back and checked against our scoring.

The fixture is six rows cut from the 18,002-row recording the organiser's
public host wrote on 2026-10-01 while v6 flew against its built-in opponent.
Our data, from our run; the host program itself is not in the repository.
"""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pytest

from competition.hostcsv import (
    NEEDED,
    angles,
    check_scoring,
    main,
    normalised,
    read_host_csv,
    summary,
)
from competition.scoring import Normalisation

SAMPLE = Path(__file__).parent / "data" / "host_round_2026-10-01_sample.csv"


def test_the_sample_reads_with_both_players_and_every_needed_column():
    hr = read_host_csv(SAMPLE)

    assert hr.frames == 6
    for player in ("player1", "player2"):
        for name in NEEDED:
            assert f"{player}_{name}" in hr.columns
    assert hr.winner[-1] == "1.0"


def test_the_host_computes_the_host_normalisation_not_the_announcements():
    """Six real frames, three columns each: the HOST formula reproduces the
    host's numbers and the announcement's does not. This is the evidence
    behind scoring.Normalisation.HOST."""
    fits = {fit.normalisation: fit for fit in check_scoring(read_host_csv(SAMPLE), "player2", "player1")}

    assert fits[Normalisation.HOST].attack_max_diff < 1e-3
    assert fits[Normalisation.HOST].position_max_diff < 1e-3
    assert fits[Normalisation.ANNOUNCEMENT].worst > 0.05


def test_player_one_matches_too_apart_from_the_ninety_degree_gate():
    fits = {fit.normalisation: fit for fit in check_scoring(read_host_csv(SAMPLE), "player1", "player2")}

    assert fits[Normalisation.HOST].attack_max_diff < 1e-3
    # One frame of the full recording sits at AA = 90.0 exactly and the host
    # pays it; the sample does not include that frame, so this is tight too.
    assert fits[Normalisation.HOST].position_max_diff < 1e-3


@pytest.mark.parametrize(
    ("angle", "expected_host", "expected_announcement"),
    [(0.0, 1.0, 1.0), (45.0, 0.75, 0.5), (89.0, 91 / 180, 1 / 90), (90.0, 0.0, 0.0), (135.0, 0.0, 0.0)],
)
def test_the_two_normalisations_share_the_gate_and_differ_in_slope(
    angle, expected_host, expected_announcement
):
    a = np.array([angle])
    assert normalised(a, Normalisation.HOST)[0] == pytest.approx(expected_host)
    assert normalised(a, Normalisation.ANNOUNCEMENT)[0] == pytest.approx(expected_announcement)


def test_the_summary_reports_the_start_the_host_gave():
    facts = summary(read_host_csv(SAMPLE))

    start = facts["start"]
    assert start["own_heading_deg"] == pytest.approx(303.0, abs=0.01)
    assert start["foe_heading_deg"] == pytest.approx(123.0, abs=0.01)
    assert start["separation_m"] == pytest.approx(1702, abs=5)
    assert start["own_alt_ft"] == pytest.approx(11499, abs=1)
    assert facts["host"]["own_hp"] == 180.0
    assert facts["host"]["winner"] == "1.0"


def test_angles_put_zero_aspect_on_the_tail():
    """A target flying straight away from us, dead ahead: track 0, aspect 0."""
    hr = read_host_csv(SAMPLE)
    hr.columns["player1_lat_deg"][:] = 25.0
    hr.columns["player1_lon_deg"][:] = 121.0
    hr.columns["player1_alt_ft"][:] = 10_000.0
    hr.columns["player1_yaw_deg"][:] = 0.0
    hr.columns["player1_pitch_deg"][:] = 0.0
    hr.columns["player2_lat_deg"][:] = 25.01  # north of us
    hr.columns["player2_lon_deg"][:] = 121.0
    hr.columns["player2_alt_ft"][:] = 10_000.0
    hr.columns["player2_yaw_deg"][:] = 0.0  # flying north, away
    hr.columns["player2_pitch_deg"][:] = 0.0

    geo = angles(hr, "player1", "player2")

    assert geo.track_deg == pytest.approx(0.0, abs=1e-6)
    assert geo.aspect_deg == pytest.approx(0.0, abs=1e-6)
    assert geo.distance_m[0] == pytest.approx(1113.2, abs=0.5)


def test_a_csv_missing_a_column_is_refused_by_name(tmp_path):
    rows = list(csv.DictReader(SAMPLE.open(encoding="utf-8-sig")))
    for row in rows:
        del row["player2_Score_RemainingHP"]
    broken = tmp_path / "broken.csv"
    with broken.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    with pytest.raises(ValueError, match="player2_Score_RemainingHP"):
        read_host_csv(broken)


def test_main_describes_a_recording_and_names_the_normalisation(capsys):
    assert main([str(SAMPLE), "--player", "player2"]) == 0
    out = capsys.readouterr().out
    assert "the host computes the host normalisation" in out
    assert "winner column" in out


def test_main_refuses_a_missing_file(tmp_path, capsys):
    assert main([str(tmp_path / "nope.csv")]) == 2
    assert "no such file" in capsys.readouterr().err
