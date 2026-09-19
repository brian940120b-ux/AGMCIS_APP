"""Training endpoint tests (PHASE 11-13)."""

from __future__ import annotations

import pytest

from training.pipeline import rl_available


def test_status_reports_what_the_stack_can_do(client):
    body = client.get("/api/training/status").json()
    assert body["available"] is rl_available()
    assert set(body["algorithms"]) == {"ppo", "sac"}
    assert body["scenario"]
    assert "ppo" in body["hyperparameters"] and "sac" in body["hyperparameters"]


def test_status_is_honest_that_training_is_not_driven_from_the_browser(client):
    """A START TRAINING button that cannot report progress or be cancelled
    would be a control that does not do what it appears to."""
    body = client.get("/api/training/status").json()
    assert body["browser_control"] is False
    assert "train.py" in body["how_to_run"]
    assert body["browser_control_note"]


def test_the_reward_is_published_term_by_term(client):
    body = client.get("/api/training/reward").json()
    assert set(body["terms"]) == set(body["weights"])
    assert "no weapon" in body["notice"]


def test_the_reward_has_no_weapon_or_targeting_term(client):
    """AIFCS models none, so it must reward none."""
    body = client.get("/api/training/reward").json()
    forbidden = ("weapon", "target", "engage", "kill", "strike", "damage")
    assert not any(word in term.lower() for term in body["terms"] for word in forbidden)


def test_models_can_be_listed_whether_or_not_the_stack_is_installed(client):
    body = client.get("/api/training/models").json()
    assert body["available"] is rl_available()
    assert isinstance(body["models"], list)
    if not rl_available():
        assert body["install_hint"]


def test_training_runs_come_from_the_run_database(client):
    body = client.get("/api/training/runs").json()
    assert isinstance(body["runs"], list)


@pytest.mark.skipif(not rl_available(), reason="RL stack not installed")
def test_the_environment_spec_is_served(client):
    body = client.get("/api/training/environment").json()
    assert body["id"] == "AIFCSCombatEnv-v0"
    assert body["observation_size"] > 0
    assert body["action_channels"] == ["aileron", "elevator", "rudder", "throttle"]
    assert body["ticks_per_step"] >= 1


def test_the_training_subsystem_reports_its_real_state(client):
    subsystems = {s["key"]: s for s in client.get("/api/system/status").json()["subsystems"]}
    training = subsystems["training"]
    if rl_available():
        assert training["state"] == "ONLINE"
        assert "train.py" in training["detail"]
    else:
        assert training["state"] == "OFFLINE"
        assert "requirements-ml" in training["detail"]
