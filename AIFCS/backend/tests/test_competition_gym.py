"""The Gymnasium wrapper (COMP PHASE 5b)."""

from __future__ import annotations

import numpy as np
import pytest

from competition.environment import EnvConfig
from competition.gym_env import CompetitionEnv, make_vec_env
from competition.rewards import RewardMode

pytest.importorskip("jsbsim")
pytest.importorskip("gymnasium")

LEVEL = np.array([0.0, 0.0, 0.0, 0.8], dtype=np.float32)


@pytest.mark.parametrize("mode", [RewardMode.REFERENCE, RewardMode.SCORE])
def test_stable_baselines_accepts_the_environment(mode):
    """SB3's own checker, because a wrong space fails at step 40,000 otherwise.

    It warns about one thing, and the warning is correct: the action space is
    not symmetric. Nor can it be — the rudder is pinned to [0, 1e-17] and the
    throttle runs [0, 1], because that is the space the organiser defines and
    the space the reference policy was trained in. So the warning is asserted
    rather than silenced, and anything else the checker has to say is a failure.
    """
    import warnings

    from stable_baselines3.common.env_checker import check_env

    with warnings.catch_warnings(record=True) as raised:
        warnings.simplefilter("always")
        check_env(CompetitionEnv(EnvConfig(), reward_mode=mode, seed=1), skip_render_check=True)

    unexpected = [
        str(warning.message)
        for warning in raised
        if "symmetric and normalized Box action space" not in str(warning.message)
    ]
    assert not unexpected, unexpected


def test_a_round_that_runs_out_of_time_is_truncated_not_terminated():
    """A five-minute round ending is not the same as an aircraft being lost.

    Getting this backwards teaches the value function that surviving to the end
    of a round is worth nothing, which is the opposite of the truth.
    """
    env = CompetitionEnv(EnvConfig(round_seconds=1.0), reward_mode=RewardMode.SCORE, seed=2)
    env.reset(seed=2)
    for _ in range(120):
        _, _, terminated, truncated, info = env.step(LEVEL)
        if terminated or truncated:
            break
    assert truncated and not terminated
    assert info["reason"] == "TIME"


def test_the_score_travels_with_the_end_of_the_episode():
    """So a run can be watched in the units the judges use."""
    env = CompetitionEnv(EnvConfig(round_seconds=1.0), seed=3)
    env.reset(seed=3)
    for _ in range(120):
        _, _, terminated, truncated, info = env.step(LEVEL)
        if terminated or truncated:
            break
    assert "score" in info
    assert set(info["score"]) >= {"attack_seconds", "position_sum", "total", "killed"}


def test_the_kill_bonus_is_not_paid_on_every_frame_after_the_kill():
    """It is a terminal reward, and a repeated one would swamp everything else."""
    env = CompetitionEnv(EnvConfig(), reward_mode=RewardMode.SCORE, seed=4)
    env.reset(seed=4)
    env.round.score.killed_at_s = 3.0
    env._previous_kill = 3.0
    _, reward, _, _, _ = env.step(LEVEL)
    assert reward < 100.0, "a repeated kill bonus would be in the hundreds"


def test_resetting_with_a_seed_gives_the_same_round_twice():
    a = CompetitionEnv(EnvConfig(), seed=7)
    b = CompetitionEnv(EnvConfig(), seed=7)
    first, _ = a.reset(seed=7)
    second, _ = b.reset(seed=7)
    np.testing.assert_allclose(first, second)


def test_one_worker_stays_out_of_the_multiprocessing_machinery():
    from stable_baselines3.common.vec_env import DummyVecEnv

    env = make_vec_env(1, EnvConfig(), RewardMode.REFERENCE, seed=0)
    assert isinstance(env, DummyVecEnv)
    env.close()


def test_the_rudder_stays_shut_unless_asked_for():
    shut = CompetitionEnv(EnvConfig(rudder_enabled=False))
    open_ = CompetitionEnv(EnvConfig(rudder_enabled=True))
    assert shut.action_space.high[2] == pytest.approx(1e-17)
    assert open_.action_space.high[2] == 1.0
