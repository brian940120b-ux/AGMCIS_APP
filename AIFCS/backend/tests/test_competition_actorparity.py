"""The parity check itself, without Stable-Baselines3 in the room."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("jsbsim")

from competition.actorparity import SAME_POLICY_TOLERANCE, compare, describe, observations_from_round
from competition.environment import EnvConfig
from competition.evaluate import neutral_policy


def test_compare_measures_the_largest_difference_per_channel():
    base = neutral_policy()

    def nudged(observation):
        return base(observation) + np.array([0.0, 2e-3, 0.0, 0.0])

    result = compare(base, nudged, [np.zeros(20), np.ones(20)])
    assert result["observations"] == 2
    assert result["max_abs_per_channel"][1] == pytest.approx(2e-3)
    assert result["max_abs_per_channel"][0] == 0.0
    assert result["same_policy"] is False
    assert "NOT the same policy" in describe(result)


def test_float32_rounding_counts_as_the_same_policy():
    base = neutral_policy()

    def rounded(observation):
        return base(observation).astype(np.float32).astype(np.float64) + 1e-7

    result = compare(base, rounded, [np.zeros(20)])
    assert result["max_abs"] < SAME_POLICY_TOLERANCE
    assert result["same_policy"] is True
    assert "same policy" in describe(result)


def test_observations_come_from_a_round_held_at_the_decision_rate():
    calls = {"n": 0}
    stick = neutral_policy()

    def counting(observation):
        calls["n"] += 1
        return stick(observation)

    kept = observations_from_round(counting, EnvConfig(action_repeat=6), seconds=1.0)
    assert len(kept) == 61  # the reset observation plus sixty frames
    assert calls["n"] == 10
    assert kept[0].shape == (20,)
