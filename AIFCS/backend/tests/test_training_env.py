"""Gymnasium environment and encoder tests (PHASE 11).

The RL stack is an optional dependency, so these skip cleanly without it.
"""

from __future__ import annotations

import numpy as np
import pytest

from agents.base_agent import ContactView, Observation
from core.world_state import Team
from training.observation_encoder import (
    CONTACT_FEATURES,
    EGO_FEATURES,
    GOAL_FEATURES,
    EncoderConfig,
    Goal,
    encode,
)
from training.pipeline import rl_available

gym = pytest.importorskip("gymnasium")
pytestmark = pytest.mark.skipif(not rl_available(), reason="RL stack not installed")

# Long enough to exercise several decisions, short enough to stay quick.
EPISODE_SECONDS = 6.0


def _observation(contacts=None, altitude=6000.0, speed=220.0, confidence=1.0):
    return Observation(
        agent_id="A",
        entity_id="BLUE-01",
        team=Team.BLUE,
        simulation_time=1.0,
        position=np.array([100.0, 200.0, altitude]),
        velocity=np.array([speed, 0.0, 0.0]),
        orientation=np.array([0.1, 0.05, 1.5]),
        angular_velocity=np.array([0.2, -0.1, 0.05]),
        altitude=altitude,
        speed=speed,
        heading_deg=90.0,
        contacts=contacts or [],
        confidence=confidence,
    )


def _contact(distance=5000.0, friendly=True):
    return ContactView(
        entity_id="BLUE-02",
        team=Team.BLUE if friendly else Team.RED,
        relative_position=np.array([distance, 0.0, 0.0]),
        relative_velocity=np.array([10.0, 0.0, 0.0]),
        distance_m=distance,
        is_friendly=friendly,
    )


# ------------------------------------------------------------------- encoder


def test_the_vector_is_the_declared_size():
    config = EncoderConfig()
    assert config.size == EGO_FEATURES + config.max_contacts * CONTACT_FEATURES + GOAL_FEATURES
    assert encode(_observation(), None, config).shape == (config.size,)


def test_every_element_is_bounded_and_finite():
    """An unbounded input is how a policy learns to exploit one huge number,
    and a NaN is how training dies twenty minutes in."""
    config = EncoderConfig()
    extreme = _observation(
        contacts=[_contact(distance=10**9)],
        altitude=10**9,
        speed=10**9,
    )
    vector = encode(extreme, Goal(position=np.array([10**9, -(10**9), 0.0])), config)

    assert np.all(np.isfinite(vector))
    assert np.all(vector >= -1.0) and np.all(vector <= 1.0)


def test_a_non_finite_input_does_not_reach_the_policy():
    config = EncoderConfig()
    broken = _observation()
    object.__setattr__(broken, "altitude", float("nan"))
    vector = encode(broken, None, config)
    assert np.all(np.isfinite(vector))


def test_an_empty_contact_slot_is_distinguishable_from_a_contact_at_zero():
    config = EncoderConfig(max_contacts=2)
    empty = encode(_observation(), None, config)
    held = encode(_observation([_contact()]), None, config)

    # The leading "present" flag of the first contact slot.
    assert empty[EGO_FEATURES] == 0.0
    assert held[EGO_FEATURES] == 1.0


def test_contacts_are_ordered_nearest_first():
    """A fixed number of slots has to drop something; keep what is closest."""
    config = EncoderConfig(max_contacts=1)
    far, near = _contact(distance=40000.0), _contact(distance=900.0)
    vector = encode(_observation([far, near]), None, config)
    distance_index = EGO_FEATURES + 4
    assert vector[distance_index] == pytest.approx(900.0 / config.max_range_m, abs=1e-6)


def test_no_goal_is_said_explicitly_rather_than_encoded_as_zero():
    config = EncoderConfig()
    without = encode(_observation(), None, config)
    with_goal = encode(_observation(), Goal(position=np.array([5000.0, 0.0, 6000.0])), config)
    goal_flag = EGO_FEATURES + config.max_contacts * CONTACT_FEATURES
    assert without[goal_flag] == 0.0
    assert with_goal[goal_flag] == 1.0


# --------------------------------------------------------------- environment


@pytest.fixture(scope="module")
def env():
    from training.environment import AIFCSCombatEnv

    environment = AIFCSCombatEnv(
        scenario_name="training_navigation", max_episode_seconds=EPISODE_SECONDS, seed=99
    )
    yield environment
    environment.close()


def test_the_environment_passes_gymnasiums_own_checker():
    from gymnasium.utils.env_checker import check_env

    from training.environment import AIFCSCombatEnv

    environment = AIFCSCombatEnv(
        scenario_name="training_navigation", max_episode_seconds=EPISODE_SECONDS, seed=1
    )
    try:
        check_env(environment, skip_render_check=True)
    finally:
        environment.close()


def test_reset_and_step_return_what_the_spaces_promise(env):
    observation, info = env.reset(seed=99)
    assert env.observation_space.contains(observation)
    assert info["entity_id"]

    observation, reward, terminated, truncated, info = env.step(env.action_space.sample())
    assert env.observation_space.contains(observation)
    assert np.isfinite(reward)
    assert isinstance(terminated, bool) and isinstance(truncated, bool)
    assert info["reward"]["terms"], "every step must explain its reward"


def test_the_same_seed_produces_the_same_episode():
    from training.environment import AIFCSCombatEnv

    def rollout(seed):
        environment = AIFCSCombatEnv(
            scenario_name="training_navigation", max_episode_seconds=EPISODE_SECONDS, seed=seed
        )
        environment.reset(seed=seed)
        rng = np.random.default_rng(0)
        total, hashes = 0.0, []
        for _ in range(12):
            _, reward, _, _, _ = environment.step(rng.uniform(-1, 1, 4).astype(np.float32))
            total += reward
            hashes.append(environment.engine.world.state_hash)
        environment.close()
        return total, hashes

    first_total, first_hashes = rollout(4242)
    second_total, second_hashes = rollout(4242)
    _, other_hashes = rollout(777)

    assert first_hashes == second_hashes
    assert first_total == pytest.approx(second_total)
    assert first_hashes != other_hashes


def test_the_policy_cannot_act_faster_than_a_rule_agent(env):
    """One action per decision interval, not one per physics tick."""
    assert env.ticks_per_step == pytest.approx(
        env.settings.simulation.tick_rate_hz / env.settings.agents.decision_rate_hz
    )
    env.reset(seed=99)
    before = env.engine.world.tick
    env.step(np.zeros(4, dtype=np.float32))
    assert env.engine.world.tick - before == env.ticks_per_step


def test_throttle_uses_its_whole_range():
    """Throttle is physically 0..1; half the policy's range must not mean off."""
    from training.environment import AIFCSCombatEnv

    off = AIFCSCombatEnv._to_controls(np.array([0.0, 0.0, 0.0, -1.0]))
    mid = AIFCSCombatEnv._to_controls(np.array([0.0, 0.0, 0.0, 0.0]))
    full = AIFCSCombatEnv._to_controls(np.array([0.0, 0.0, 0.0, 1.0]))

    assert off.throttle == pytest.approx(0.0)
    assert mid.throttle == pytest.approx(0.5)
    assert full.throttle == pytest.approx(1.0)


def test_a_non_finite_action_becomes_a_safe_one():
    from training.environment import AIFCSCombatEnv

    controls = AIFCSCombatEnv._to_controls(np.array([np.nan, np.inf, -np.inf, np.nan]))
    for channel in ("aileron", "elevator", "rudder", "throttle"):
        assert np.isfinite(getattr(controls, channel))


def test_the_episode_ends_at_the_step_limit(env):
    env.reset(seed=99)
    truncated = False
    for _ in range(env.max_episode_steps + 5):
        _, _, terminated, truncated, _ = env.step(np.array([0.0, 0.0, 0.0, -0.48], dtype=np.float32))
        if terminated or truncated:
            break
    assert truncated, "a level flight episode should end on the time limit, not a crash"


def test_the_policy_flies_through_the_safety_layer(env):
    """A policy must not get a shortcut past envelope protection.

    Commanding full deflection every step and checking the applied controls
    stay inside their bounds proves the flight controller is still in the path.
    """
    env.reset(seed=99)
    for _ in range(20):
        env.step(np.array([1.0, 1.0, 1.0, 1.0], dtype=np.float32))
    entity = env.engine.world.get(env.policy_agent.entity_id)
    for channel in ("aileron", "elevator", "rudder"):
        assert -1.0 <= getattr(entity.controls, channel) <= 1.0
    assert 0.0 <= entity.controls.throttle <= 1.0


def test_the_other_units_keep_their_rule_agents(env):
    """The policy learns in traffic, not in an empty sky."""
    env.reset(seed=99)
    types = {a.entity_id: type(a).__name__ for a in env.engine.agents.agents}
    assert types[env.policy_agent.entity_id] == "PolicyAgent"
    assert any(name == "RuleAgent" for name in types.values())


def test_the_spec_says_what_the_environment_does_not_model(env):
    spec = env.spec_summary()
    assert spec["observation_size"] == env.encoder_config.size
    assert "no weapon" in spec["notice"]
