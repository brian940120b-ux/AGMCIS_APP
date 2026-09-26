"""The Gymnasium wrapper, and the vectorised factory training needs.

`CompetitionRound` does the flying and `CompetitionEnv` does the Gymnasium
protocol around it. Keeping them apart means a round can be stepped in a test,
scored from a replay, or driven by the UDP client without any of that importing
Gymnasium.

Every episode's `info` carries the competition score, so a training run can be
watched in the units the judges use rather than in whatever units its reward
happens to be in. That distinction is the whole reason there are two rewards.
"""

from __future__ import annotations

from typing import Any

import gymnasium as gym
import numpy as np

from competition.environment import (
    SIM_HZ,
    CompetitionRound,
    EnvConfig,
    action_space,
    observation_space,
)
from competition.rewards import ReferenceReward, RewardMode, ScoreReward, ShapedReward
from competition.state import Geometry
from core.logging_config import get_logger

log = get_logger("competition.gym")


class CompetitionEnv(gym.Env[np.ndarray, np.ndarray]):
    """One F-16 against one opponent, for five minutes, at 60 Hz.

    Nothing here models a weapon. The four channels are stick and throttle, and
    "attack" is a geometric condition the competition host computes — which is
    what the published interface allows a contestant to send and no more.
    """

    metadata = {"render_modes": []}

    def __init__(
        self,
        config: EnvConfig | None = None,
        reward_mode: RewardMode | str = RewardMode.REFERENCE,
        seed: int | None = None,
    ) -> None:
        super().__init__()
        self.config = config or EnvConfig()
        self.reward_mode = RewardMode(reward_mode)
        self.observation_space = observation_space()
        self.action_space = action_space(self.config.rudder_enabled)
        self.round = CompetitionRound(self.config, seed=seed)
        self._reward = self._build_reward()
        self._previous_kill: float | None = None
        self._previous_foe_kill: float | None = None

    def _build_reward(self) -> ReferenceReward | ScoreReward | ShapedReward:
        if self.reward_mode is RewardMode.REFERENCE:
            return ReferenceReward()
        if self.reward_mode is RewardMode.SHAPED:
            return ShapedReward(
                weights=self.config.weights,
                envelope=self.config.envelope,
                tick_hz=float(SIM_HZ),
            )
        return ScoreReward(
            weights=self.config.weights,
            envelope=self.config.envelope,
            tick_hz=float(SIM_HZ),
            # `score` is the competition's own number for one side; `margin` is
            # what the rules compare when a round reaches time.
            defensive=self.reward_mode is RewardMode.MARGIN,
        )

    # ------------------------------------------------------------ gymnasium

    def reset(
        self, *, seed: int | None = None, options: dict[str, Any] | None = None
    ) -> tuple[np.ndarray, dict[str, Any]]:
        super().reset(seed=seed)
        state = self.round.reset(seed=seed)
        self._reward.reset()
        self._previous_kill = None
        self._previous_foe_kill = None
        return state, {}

    def step(self, action: np.ndarray) -> tuple[np.ndarray, float, bool, bool, dict[str, Any]]:
        """One decision, held for `action_repeat` frames of physics.

        The rewards of the held frames are summed rather than averaged, because
        a frame's reward is a rate — points per frame — and holding a good
        position for six frames is worth six frames of it. Averaging would make
        a decision that lasts longer worth no more than one that does not.
        """
        raw = np.asarray(action, dtype=np.float64)
        reward = 0.0
        for _ in range(self.config.action_repeat):
            state, _, finished, reason, frame_reward = self._frame(raw)
            reward += frame_reward
            if finished:
                break

        truncated = reason == "TIME"
        terminated = finished and not truncated
        info: dict[str, Any] = {"reason": reason} if finished else {}
        if finished:
            info["score"] = self.round.score.as_dict()
            info["opponent_score"] = self.round.opponent_score.as_dict()
        return state, float(reward), terminated, truncated, info

    def _frame(self, raw: np.ndarray) -> tuple[np.ndarray, Geometry, bool, str, float]:
        state, geometry, finished, reason = self.round.step(raw)

        crashed = reason == "CRASH"
        foe_crashed = reason == "FOE_CRASH"
        # Paid once, on the frame the third second completes.
        killed_at = self.round.score.killed_at_s
        newly_killed = killed_at if killed_at != self._previous_kill else None
        self._previous_kill = killed_at

        if isinstance(self._reward, ReferenceReward):
            reward = self._reward(geometry, crashed=crashed, foe_crashed=foe_crashed)
        else:
            foe_killed_at = self.round.opponent_score.killed_at_s
            foe_newly_killed = foe_killed_at if foe_killed_at != self._previous_foe_kill else None
            self._previous_foe_kill = foe_killed_at
            reward = self._reward(
                geometry,
                g_load=self.round.own.g_load,
                crashed=crashed,
                foe_crashed=foe_crashed,
                killed_at_s=newly_killed,
                # The opponent's side of the same frame. Computed by the round
                # already, because it scores both sides to decide the winner.
                foe_geometry=self.round.foe_geometry(),
                foe_g_load=self.round.foe.g_load,
                foe_killed_at_s=foe_newly_killed,
            )

        return state, geometry, finished, reason, float(reward)


def make_env(
    config: EnvConfig | None = None,
    reward_mode: RewardMode | str = RewardMode.REFERENCE,
    seed: int | None = None,
):
    """A thunk, because SubprocVecEnv wants one per worker.

    The seed is offset per worker by the caller: identical seeds across eight
    processes would be eight copies of one round, and a batch of eight identical
    trajectories teaches a fraction of what eight different ones do.
    """

    def build() -> CompetitionEnv:
        return CompetitionEnv(config=config, reward_mode=reward_mode, seed=seed)

    return build


def make_vec_env(
    workers: int,
    config: EnvConfig | None = None,
    reward_mode: RewardMode | str = RewardMode.REFERENCE,
    seed: int = 0,
    monitor: bool = True,
    start_method: str | None = None,
):
    """`workers` rounds in parallel, one process each.

    Processes rather than threads because JSBSim is the cost and it holds the
    GIL: measured single-process throughput is about 3,000 steps/s, and the
    gradient step is what a GPU is for. Falls back to a single in-process
    environment when one worker is asked for, which keeps a debugging run out of
    the multiprocessing machinery.

    **The caller must be an importable module guarded by `if __name__ ==
    "__main__":`.** Windows has no `fork`, so each worker starts a fresh
    interpreter and re-imports the launching module; without the guard it
    re-runs the training script, which spawns more workers, which re-import it.
    `start_method` defaults to what Stable-Baselines3 picks, which is the right
    answer on both platforms; it is exposed for the one case where it is not.
    """
    from stable_baselines3.common.monitor import Monitor
    from stable_baselines3.common.vec_env import DummyVecEnv, SubprocVecEnv

    def thunk(rank: int):
        def build() -> gym.Env:
            env: gym.Env = CompetitionEnv(config=config, reward_mode=reward_mode, seed=seed + rank)
            return Monitor(env) if monitor else env

        return build

    builders = [thunk(rank) for rank in range(workers)]
    if workers == 1:
        return DummyVecEnv(builders)
    if start_method is None:
        return SubprocVecEnv(builders)
    return SubprocVecEnv(builders, start_method=start_method)
