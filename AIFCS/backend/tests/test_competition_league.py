"""Choosing who to fight next (COMP PHASE 15).

The thresholds are not invented. They are PHANG-MAN's, from the AlphaDogfight
Trials paper: sampling stays uniform until every opponent has been met a
hundred times and the agent is winning overall, then goes proportional to how
often each one beats it, clipped to between 0.2% and 11.7%.

These are arithmetic tests. Whether a league trains a better policy is a
question for a GPU; whether the distribution does what the paper describes is
a question with an answer here.
"""

from __future__ import annotations

import pytest

from competition.league import GATE_WIN_RATE, MAX_SHARE, MIN_SHARE, WINDOW, League


def played(league: League, name: str, wins: int, losses: int) -> None:
    for _ in range(wins):
        league.record(name, True)
    for _ in range(losses):
        league.record(name, False)


def test_an_empty_league_is_refused():
    with pytest.raises(ValueError):
        League(names=[])


def test_sampling_is_uniform_before_anyone_has_been_met():
    league = League(names=["drone", "pursuit", "old"])
    assert not league.weighting
    assert list(league.shares().values()) == pytest.approx([1 / 3] * 3)


def test_it_stays_uniform_while_the_agent_is_losing():
    """Weighting towards whoever beats you, while everyone beats you, is just
    uniform with extra steps — and the paper gates on winning for that reason."""
    league = League(names=["a", "b"])
    for name in ("a", "b"):
        played(league, name, wins=10, losses=WINDOW - 10)

    assert league.overall_win_rate == pytest.approx(0.1)
    assert not league.weighting
    assert list(league.shares().values()) == pytest.approx([0.5, 0.5])


def test_it_stays_uniform_until_everyone_has_been_met_enough():
    """The other half of the gate: a hundred matchups each."""
    league = League(names=["a", "b"])
    played(league, "a", wins=90, losses=10)
    played(league, "b", wins=9, losses=1)  # only ten so far

    assert league.overall_win_rate > GATE_WIN_RATE
    assert not league.weighting, "b has not been played the full window"


def test_once_it_is_winning_the_hard_opponents_come_up_more():
    league = League(names=["easy", "hard"])
    played(league, "easy", wins=95, losses=5)
    played(league, "hard", wins=55, losses=45)

    assert league.weighting
    shares = league.shares()
    assert shares["hard"] > shares["easy"]
    assert sum(shares.values()) == pytest.approx(1.0)


def test_nobody_is_ever_dropped_entirely():
    """An opponent beaten every single time still comes up, because a policy
    that stops meeting something forgets how to beat it."""
    league = League(names=["beaten", "hard", "harder"])
    played(league, "beaten", wins=WINDOW, losses=0)
    played(league, "hard", wins=60, losses=40)
    played(league, "harder", wins=51, losses=49)

    shares = league.shares()
    assert shares["beaten"] > 0.0
    assert sum(shares.values()) == pytest.approx(1.0)


def test_beating_everyone_every_time_falls_back_to_uniform():
    """Otherwise the weights are all zero and the distribution is undefined."""
    league = League(names=["a", "b"])
    for name in ("a", "b"):
        played(league, name, wins=WINDOW, losses=0)

    assert list(league.shares().values()) == pytest.approx([0.5, 0.5])


def test_only_the_last_hundred_matchups_count():
    """A window, not a history: an opponent that used to be hard and is not any
    more should stop being oversampled."""
    league = League(names=["a"])
    played(league, "a", wins=0, losses=WINDOW)
    assert league.records["a"].win_rate == 0.0

    played(league, "a", wins=WINDOW, losses=0)
    assert league.records["a"].played == WINDOW, "the old results fell out"
    assert league.records["a"].win_rate == 1.0


def test_an_unplayed_opponent_is_neither_feared_nor_dismissed():
    assert League(names=["new"]).records["new"].win_rate == 0.5


def test_the_clips_are_the_papers():
    assert MIN_SHARE == 0.002
    assert MAX_SHARE == 0.117
    assert WINDOW == 100
    assert GATE_WIN_RATE == 0.5


def test_choosing_is_reproducible_from_the_seed():
    """A training run has to be repeatable, and the opponent order is part of
    what a run was."""
    first = League(names=["a", "b", "c"], seed=7)
    second = League(names=["a", "b", "c"], seed=7)
    assert [first.next_opponent() for _ in range(20)] == [second.next_opponent() for _ in range(20)]


# ----------------------------------------------------- the pool, end to end

jsbsim = pytest.importorskip("jsbsim")

import numpy as np

from competition.action import INITIAL_THROTTLE
from competition.environment import EnvConfig
from competition.gym_env import CompetitionEnv
from competition.league import PolicyOpponent, collect_checkpoints
from competition.safety import GroundAvoidance
from competition.scoring import Verdict, decide_round


def constant(elevator: float) -> PolicyOpponent:
    """A stand-in for a trained policy: enough to fly, enough to differ."""
    return PolicyOpponent(lambda state: np.array([0.0, elevator, 0.0, 1.0]))


def test_the_environment_draws_an_opponent_per_round_and_records_the_result():
    pool = {"gentle": constant(-0.05), "hard": constant(-0.4), "diving": constant(0.2)}
    env = CompetitionEnv(
        EnvConfig(opponent_pool=pool, ground_avoidance=GroundAvoidance()),
        reward_mode="margin",
        seed=3,
    )
    hold = np.array([0.0, 0.0, 0.0, INITIAL_THROTTLE])

    faced = []
    for episode in range(4):
        env.reset(seed=100 + episode)
        while True:
            _, _, terminated, truncated, info = env.step(hold)
            if terminated or truncated:
                break
        faced.append(info["opponent_name"])

    assert set(faced) <= set(pool), "only opponents from the pool"
    assert len(set(faced)) > 1, "and not the same one every time"
    league = env._league
    assert league is not None
    assert sum(r.played for r in league.records.values()) == 4, "every round was recorded"


def test_the_result_recorded_is_the_organisers_verdict():
    """Not "did the reward go up" — 表 3, the same table that decides the day.

    Checked against `decide_round` recomputed from the finished round rather
    than against a predicted outcome: a first version assumed a steeply diving
    opponent would hit the ground inside five minutes, and it did not. What
    matters is that the league and the rules agree, whatever happened.
    """
    pool = {"diving": constant(0.9)}
    env = CompetitionEnv(
        EnvConfig(opponent_pool=pool, ground_avoidance=GroundAvoidance()),
        reward_mode="margin",
        seed=5,
    )
    env.reset(seed=100)
    hold = np.array([0.0, 0.0, 0.0, INITIAL_THROTTLE])
    while True:
        _, _, terminated, truncated, info = env.step(hold)
        if terminated or truncated:
            break

    reason = info["reason"]
    expected = decide_round(
        env.round.score,
        env.round.opponent_score,
        blue_crashed=reason == "CRASH",
        red_crashed=reason == "FOE_CRASH",
        collided=reason == "COLLISION",
    )
    league = env._league
    assert league is not None
    won = expected.verdict is Verdict.BLUE
    assert league.records["diving"].wins == (1 if won else 0)
    assert league.records["diving"].played == 1


def test_no_pool_means_no_league_and_the_scripted_opponent_stands():
    env = CompetitionEnv(EnvConfig(), reward_mode="margin", seed=1)
    assert env._league is None


def test_a_directory_of_checkpoints_becomes_a_pool(tmp_path):
    """So self-play is snapshotting into a folder, not editing a command line."""
    for name in ("run1", "run2"):
        (tmp_path / f"{name}.zip").write_bytes(b"not really a model")

    found = collect_checkpoints([str(tmp_path)])
    assert sorted(found) == ["run1", "run2"]


def test_a_session_directorys_checkpoint_is_named_after_the_session(tmp_path):
    """`checkpoint.zip` tells a reader nothing; the session name tells them
    which run they are fighting."""
    session = tmp_path / "v2"
    session.mkdir()
    (session / "checkpoint.zip").write_bytes(b"not really a model")

    assert sorted(collect_checkpoints([str(session / "checkpoint.zip")])) == ["v2"]


def test_an_empty_or_missing_pool_says_so(tmp_path):
    with pytest.raises(FileNotFoundError):
        collect_checkpoints([str(tmp_path)])
    with pytest.raises(FileNotFoundError):
        collect_checkpoints([str(tmp_path / "nothing.zip")])
