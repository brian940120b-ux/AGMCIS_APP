"""Holding a decision for several frames, on both sides of the wire.

Action repeat changes the plant. A policy trained deciding ten times a second
and flown deciding sixty times a second is flying an aircraft it has never met,
and the failure would show up on competition day rather than in training — so
what these check is not that the idea is good, but that the two halves agree.

Legality: 注意事項 10 requires a command every frame, and a repeated decision
sends one. What changes is how often the policy is asked, not how often the
host is answered.
"""

from __future__ import annotations

import numpy as np
import pytest

from competition.client import CompetitionClient
from competition.protocol import OBS_STRUCT, decode_command


class Counting:
    """A policy that returns something new each time it is asked."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, state: np.ndarray) -> np.ndarray:
        self.calls += 1
        return np.array([0.5, -0.5, 0.0, 0.8], dtype=np.float64)


def observation(latitude: float) -> bytes:
    values = [0.0] * 26
    values[0] = latitude  # own latitude, so each frame is a new position
    values[1] = 121.0
    values[2] = 15_000.0
    values[20] = 23.1
    values[21] = 121.1
    values[22] = 15_000.0
    return OBS_STRUCT.pack(*values)


@pytest.mark.parametrize("repeat", [1, 2, 6])
def test_the_policy_is_asked_once_per_repeat(repeat: int):
    policy = Counting()
    client = CompetitionClient(policy, action_repeat=repeat)

    frames = 60
    for step in range(frames):
        assert client.on_observation(observation(23.0 + step * 1e-4)) is not None

    assert policy.calls == frames // repeat + (1 if frames % repeat else 0)
    assert client.stats.decisions_made == policy.calls


def test_a_command_still_goes_back_on_every_frame():
    """The rule is about the reply, not the decision. 注意事項 10."""
    client = CompetitionClient(Counting(), action_repeat=6)
    for step in range(30):
        reply = client.on_observation(observation(23.0 + step * 1e-4))
        assert reply is not None, "every frame is answered"
        decode_command(reply)  # and with a well-formed packet


def test_a_held_decision_does_not_cross_a_round_boundary():
    """A new round starts from the policy, not from what the last one was doing."""
    policy = Counting()
    client = CompetitionClient(policy, action_repeat=6)

    for step in range(4):
        client.on_observation(observation(23.0 + step * 1e-4))
    assert policy.calls == 1

    client.begin_round()
    client.on_observation(observation(24.0))
    assert policy.calls == 2, "the first frame of a round asks again"


def test_the_repeat_has_to_be_a_number_of_frames():
    with pytest.raises(ValueError):
        CompetitionClient(Counting(), action_repeat=0)


# ------------------------------------------------ the training side agrees

jsbsim = pytest.importorskip("jsbsim")

from competition.environment import EnvConfig
from competition.gym_env import CompetitionEnv


def test_one_env_step_advances_the_repeat_in_frames():
    """`--timesteps` counts decisions once a decision lasts more than a frame.

    Worth pinning: the same number means six times as much flying at a repeat
    of six, and a run configured by copying a step count from another run would
    silently be six times longer or shorter.
    """
    action = np.array([0.0, -0.1, 0.0, 0.8])

    single = CompetitionEnv(EnvConfig(action_repeat=1), reward_mode="margin", seed=7)
    single.reset(seed=7)
    for _ in range(6):
        single.step(action)

    repeated = CompetitionEnv(EnvConfig(action_repeat=6), reward_mode="margin", seed=7)
    repeated.reset(seed=7)
    repeated.step(action)

    assert repeated.round.frame == single.round.frame == 6


def test_the_two_paths_fly_the_same_aircraft():
    """The whole point of one copy of the shaping: a repeat has to mean the
    same thing in training and on the day.

    Both sides are driven with a constant action, so any difference in *when*
    the policy is consulted shows up as a difference in the commanded stick.
    """
    config = EnvConfig(action_repeat=6)
    env = CompetitionEnv(config, reward_mode="margin", seed=11)
    env.reset(seed=11)
    for _ in range(5):
        env.step(np.array([0.5, -0.5, 0.0, 0.8]))

    client = CompetitionClient(Counting(), action_repeat=6)
    for step in range(30):
        client.on_observation(observation(23.0 + step * 1e-4))

    # 30 frames of a 6-frame repeat is five decisions on both sides.
    assert env.round.frame == 30
    assert client.stats.decisions_made == 5
