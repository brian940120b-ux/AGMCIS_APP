"""The host's own CSV, read back and checked against our scoring.

The fixture is six rows cut from the 18,002-row recording the organiser's
public host wrote on 2026-10-01 while v6 flew against its built-in opponent.
Our data, from our run; the host program itself is not in the repository.
"""

from __future__ import annotations

import csv
import functools
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


def test_the_scoring_engine_itself_reproduces_the_hosts_columns_on_the_sample():
    """Through `StateEncoder` and `position_advantage`, not hostcsv's own angles:
    this is the path evaluate, scoreboard and the shaped reward take. Before
    2026-10-01 it read the aspect angle backwards and this would have failed by
    about 0.9 on the frames where we sit on the target's tail."""
    import numpy as np

    from competition.scoring import position_advantage
    from competition.state import StateEncoder, Telemetry

    hr = read_host_csv(SAMPLE)
    encoder = StateEncoder()

    def column(player: str, name: str, frame: int) -> float:
        return float(hr.col(player, name)[frame])

    for i in range(hr.frames):
        g = functools.partial(column, "player1", frame=i)
        e = functools.partial(column, "player2", frame=i)
        vn, ve, vd = g("vn_fps"), g("ve_fps"), g("vd_fps")
        vt = (vn * vn + ve * ve + vd * vd) ** 0.5
        values = np.array(
            [
                g("lat_deg"),
                g("lon_deg"),
                g("alt_ft"),
                g("roll_deg"),
                g("pitch_deg"),
                g("yaw_deg"),
                vn,
                ve,
                vd,
                g("p_radps"),
                g("q_radps"),
                g("r_radps"),
                g("ias_kts") * 1.68781,
                vt,
                g("g_acc"),
                vt,
                0.0,
                0.0,
                0.0,
                0.0,
                e("lat_deg"),
                e("lon_deg"),
                e("alt_ft"),
                e("vn_fps"),
                e("ve_fps"),
                e("vd_fps"),
            ]
        )
        ours = position_advantage(encoder.geometry(Telemetry.from_observation(values)), Normalisation.HOST)
        host = g("Score_AttackAdvantage") + g("Score_PositionAdvantage")
        # The host reads the target's nose; the encoder reads its velocity. For
        # a level PID target they differ by its angle of attack, a degree or two.
        assert ours == pytest.approx(host, abs=0.05), f"frame {i}: ours {ours:.3f} host {host:.3f}"


# ------------------------------------------- the second round: the cone


SAMPLE2 = Path(__file__).parent / "data" / "host_round_2026-10-03_sample.csv"


def _telemetry_from_row(hr, i: int):
    import numpy as np

    from competition.state import Telemetry

    g = functools.partial(lambda p, k, f: float(hr.col(p, k)[f]), "player1", f=i)
    e = functools.partial(lambda p, k, f: float(hr.col(p, k)[f]), "player2", f=i)
    vn, ve, vd = g("vn_fps"), g("ve_fps"), g("vd_fps")
    vt = (vn * vn + ve * ve + vd * vd) ** 0.5
    return Telemetry.from_observation(
        np.array(
            [
                g("lat_deg"),
                g("lon_deg"),
                g("alt_ft"),
                g("roll_deg"),
                g("pitch_deg"),
                g("yaw_deg"),
                vn,
                ve,
                vd,
                g("p_radps"),
                g("q_radps"),
                g("r_radps"),
                g("ias_kts") * 1.68781,
                vt,
                g("g_acc"),
                vt,
                0.0,
                0.0,
                0.0,
                0.0,
                e("lat_deg"),
                e("lon_deg"),
                e("alt_ft"),
                e("vn_fps"),
                e("ve_fps"),
                e("vd_fps"),
            ]
        )
    )


def test_the_host_docks_one_hp_per_frame_inside_our_envelope():
    """v9_doctrine on the real host, 2026-10-03: the host took 131 HP over the
    131 frames our own AttackEnvelope (half-angle 1 degree, 500-3000 ft) says
    were inside the cone, and no others. Eleven rows of it here, in consecutive
    pairs: HP drops by exactly one on the frames our envelope contains."""
    from competition.scoring import AttackEnvelope
    from competition.state import StateEncoder

    hr = read_host_csv(SAMPLE2)
    envelope = AttackEnvelope()
    encoder = StateEncoder()
    pairs = [(0, 1), (2, 3), (3, 4), (5, 6), (7, 8), (9, 10)]
    seen_cone = 0
    for before, after in pairs:
        inside = envelope.contains(encoder.geometry(_telemetry_from_row(hr, after)))
        dropped = (
            hr.col("player2", "Score_RemainingHP")[before] - hr.col("player2", "Score_RemainingHP")[after]
        )
        assert dropped == (1.0 if inside else 0.0), (
            f"rows {before}->{after}: inside {inside}, dropped {dropped}"
        )
        seen_cone += inside
    assert seen_cone == 3, "the fixture carries three in-cone transitions"


def test_the_attack_time_weight_is_ten_thousand_per_second():
    """Final gains Att + Pos every frame and 166.667 more on a cone frame:
    W_time = 10,000 per second, not the announcement's reference 2,000."""
    hr = read_host_csv(SAMPLE2)
    final = hr.col("player1", "Score_FinalAdvantage")
    att = hr.col("player1", "Score_AttackAdvantage")
    pos = hr.col("player1", "Score_PositionAdvantage")
    hp = hr.col("player2", "Score_RemainingHP")
    for before, after in [(0, 1), (2, 3), (3, 4), (5, 6), (7, 8), (9, 10)]:
        extra = (final[after] - final[before]) - (att[after] + pos[after])
        docked = hp[before] - hp[after] == 1.0
        assert extra == pytest.approx(10_000.0 / 60.0 if docked else 0.0, abs=0.01), f"rows {before}->{after}"
    facts = summary(hr)
    assert facts["attack_weight_per_s"] == pytest.approx(10_000.0, abs=1.0)
    assert facts["foe_hp_lost"] == hp[0] - hp[-1]


def test_the_summary_counts_time_under_the_collision_radius():
    hr = read_host_csv(SAMPLE2)
    facts = summary(hr)
    # Rows 7 and 8 of the fixture are the 4.3 m pass and the frame after it.
    assert facts["seconds_under_15m"] == pytest.approx(2 / 60.0, abs=1e-6)


def test_the_measured_host_weights_are_a_named_set():
    from competition.scoring import ScoringWeights

    host = ScoringWeights.host_measured()
    assert host.attack_time == 10_000.0
    assert host.position == 1.0
    assert host.kill_base == ScoringWeights().kill_base, "not exercised, so not changed"
