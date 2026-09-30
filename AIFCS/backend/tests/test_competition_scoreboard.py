"""The fixed bench.

Until now "this generation beats the last" rested on v4 and v5 — two learned
opponents that move whenever they are retrained, one of which a centred stick
beats 80% of the time. A ruler whose marks move is not a ruler.
"""

from __future__ import annotations

import pytest

pytest.importorskip("jsbsim")

from competition.adversaries import ADVERSARIES
from competition.evaluate import Report, RoundReport
from competition.scoreboard import BENCH, table
from competition.scoring import EndReason, RoundOutcome, Verdict


def _report(label: str, won: bool, margin: float, cone: float) -> Report:
    outcome = RoundOutcome(
        verdict=Verdict.BLUE if won else Verdict.RED,
        reason=EndReason.TIME,
        blue={"killed": False, "advantage_score": margin, "attack_seconds": cone},
        red={"killed": False, "advantage_score": 0.0, "attack_seconds": 0.0},
    )
    return Report(
        label=label,
        rounds=[
            RoundReport(
                seed=0,
                outcome=outcome,
                frames=18_000,
                min_distance_m=300.0,
                mean_distance_m=900.0,
                seconds_in_sweet_spot=1.0,
            )
        ],
    )


def test_the_bench_is_every_scripted_opponent_plus_the_hosts_own():
    """The scripted set is the part that cannot drift; `reference` stays
    because it is the one opponent we know the host also has."""
    assert "reference" in BENCH
    for name in ADVERSARIES:
        assert name in BENCH, f"{name} is not on the bench"


def test_the_bench_runs_easiest_first():
    """So a row reads left to right as the difficulty climbs, and a policy that
    falls over somewhere shows where."""
    assert BENCH[0] == "reference"
    assert BENCH[-1] == "wanderer"


def test_the_table_puts_sessions_down_and_opponents_across():
    board = {
        "v6": {"reference": _report("a", True, 5501.0, 2.22)},
        "do nothing": {"reference": _report("b", False, 121.0, 0.0)},
    }

    text = table(board, ("reference",), "won")
    lines = text.splitlines()

    assert "reference" in lines[0]
    assert lines[2].startswith("v6")
    assert "100%" in lines[2]
    assert lines[3].startswith("do nothing")
    assert "0%" in lines[3]


def test_a_missing_pairing_is_a_dash_not_a_zero():
    """A session that could not be scored against an opponent has no number,
    and printing 0% for it would read as a loss."""
    board = {"v6": {"reference": _report("a", True, 1.0, 1.0)}}

    text = table(board, ("reference", "break"), "won")

    assert text.splitlines()[2].rstrip().endswith("-")


@pytest.mark.parametrize("metric", ["won", "margin", "cone", "killed"])
def test_every_metric_renders(metric: str):
    board = {"v6": {"reference": _report("a", True, 5501.0, 2.22)}}

    assert table(board, ("reference",), metric).splitlines()[2].startswith("v6")


def test_the_cone_metric_is_the_best_round_not_the_mean():
    """0.38 s mean and 2.22 s best are the same policy. The board shows how
    close the best case came to three seconds, because that is the one that
    says whether a kill is within reach."""
    board = {"v6": {"reference": _report("a", True, 0.0, 2.22)}}

    assert "2.22" in table(board, ("reference",), "cone")
