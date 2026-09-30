"""The win rate as the training process sees it, and the stop rule on it
(AIFCS 2.0 hypothesis H3: exploiters, SRC-012 and SRC-007)."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from competition.environment import EnvConfig
from competition.gym_env import CompetitionEnv
from competition.safety import GroundAvoidance
from competition.winrate import WINDOW, WinRate


def infos(*verdicts: str, opponent: str | None = None) -> list[dict]:
    out = []
    for verdict in verdicts:
        info = {"verdict": verdict}
        if opponent is not None:
            info["opponent_name"] = opponent
        out.append(info)
    return out


def test_no_rounds_means_no_rate():
    callback = WinRate()
    assert callback.win_rate is None
    callback.observe([{}, {"episode": {"r": 1.0}}])
    assert callback.win_rate is None, "an info without a verdict is a frame, not a round"


def test_the_rate_is_wins_over_the_window():
    callback = WinRate(window=4)
    callback.observe(infos("BLUE", "RED", "BLUE", "BLUE"))
    assert callback.win_rate == pytest.approx(0.75)
    callback.observe(infos("RED", "RED"))
    assert callback.win_rate == pytest.approx(0.5), "the two oldest wins fell out of the window"
    assert callback.rounds == 6


def test_draws_and_replays_count_as_not_won():
    callback = WinRate()
    callback.observe(infos("DRAW", "REPLAY", "BLUE"))
    assert callback.win_rate == pytest.approx(1 / 3)


def test_rates_are_kept_per_opponent_too():
    callback = WinRate()
    callback.observe(infos("BLUE", "BLUE", opponent="break"))
    callback.observe(infos("RED", opponent="v5"))
    assert callback.rates_by_opponent() == {"break": 1.0, "v5": 0.0}


class _Logger:
    def __init__(self) -> None:
        self.records: dict[str, float] = {}

    def record(self, key: str, value: float) -> None:
        self.records[key] = value


def _armed(callback: WinRate, step_infos: list[dict]) -> WinRate:
    """Stand in for what Stable-Baselines3 hands a callback each step."""
    callback.locals = {"infos": step_infos}
    # SB3 reads `callback.logger` through the model it is attached to.
    callback.model = SimpleNamespace(logger=_Logger())
    return callback


def test_it_logs_the_rate_and_keeps_going_without_a_target():
    callback = _armed(WinRate(), infos("BLUE", "RED", opponent="break"))
    assert callback._on_step() is True
    assert callback.logger.records["league/win_rate"] == pytest.approx(0.5)
    assert callback.logger.records["league/rounds"] == 2
    assert callback.logger.records["league/win_rate_vs_break"] == pytest.approx(0.5)


def test_the_stop_waits_for_enough_rounds_then_fires(capsys):
    callback = WinRate(stop_at=0.7, min_rounds=5, window=5)
    _armed(callback, infos("BLUE", "BLUE", "BLUE", "BLUE"))
    assert callback._on_step() is True, "four wins, but the rule wants five rounds first"
    assert callback.reached is False
    _armed(callback, infos("RED"))
    assert callback._on_step() is False, "80% over five rounds clears 70%"
    assert callback.reached is True
    assert "clears 70%" in capsys.readouterr().out


def test_a_rate_below_the_target_never_stops():
    callback = WinRate(stop_at=0.9, min_rounds=2, window=2)
    _armed(callback, infos("BLUE", "RED"))
    assert callback._on_step() is True and callback.reached is False


def test_a_step_with_no_round_ending_logs_nothing():
    callback = _armed(WinRate(), [{}, {}])
    assert callback._on_step() is True
    assert callback.logger.records == {}


def test_the_bounds_are_checked():
    with pytest.raises(ValueError, match="win rate"):
        WinRate(stop_at=1.5)
    with pytest.raises(ValueError, match="win rate"):
        WinRate(stop_at=0.0)
    with pytest.raises(ValueError, match="counts of rounds"):
        WinRate(min_rounds=0)
    assert WINDOW == 50


def test_every_episode_carries_its_verdict_league_or_not():
    """The environment says who won by 表 3 on every ending, so the callback
    has something to read even when there is no pool."""
    env = CompetitionEnv(EnvConfig(ground_avoidance=GroundAvoidance()), reward_mode="margin", seed=4)
    env.reset(seed=21)
    hold = np.array([0.0, 0.0, 0.0, 0.8])
    while True:
        _, _, terminated, truncated, info = env.step(hold)
        if terminated or truncated:
            break
    assert info["verdict"] in ("BLUE", "RED", "DRAW", "REPLAY")
    assert "opponent_name" not in info, "no league, so no name"


def test_the_flags_parse_and_default_to_never_stopping():
    from competition import train

    args = train.parse_args(["--name", "x"])
    assert args.stop_at_win_rate is None and args.win_window == 50
    args = train.parse_args(["--name", "x", "--stop-at-win-rate", "0.7", "--win-window", "30"])
    assert args.stop_at_win_rate == 0.7 and args.win_window == 30


def test_the_state_remembers_why_a_run_stopped(tmp_path):
    from competition.session import Session, SessionState

    (tmp_path / "x").mkdir()
    session = Session(tmp_path / "x")
    session.write_state(SessionState(name="x", algorithm="sac", stopped_at_win_rate=0.72))
    read = session.read_state()
    assert read is not None and read.stopped_at_win_rate == 0.72
    assert SessionState(name="y", algorithm="sac").stopped_at_win_rate is None
