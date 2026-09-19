"""Tests for the YAML configuration system (PHASE 0)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.config import (
    SimulationSettings,
    WorldBounds,
    load_settings,
)


def test_loads_project_configs(settings):
    assert settings.app_name == "AIFCS"
    assert settings.simulation.tick_rate_hz == 60
    assert settings.simulation.default_speed in settings.simulation.allowed_speeds


def test_fixed_timestep_matches_tick_rate(settings):
    assert settings.simulation.dt == pytest.approx(1.0 / 60.0)


def test_config_hash_is_stable_and_seed_sensitive():
    a = load_settings()
    b = load_settings()
    assert a.config_hash == b.config_hash, "same config must hash identically"

    c = load_settings()
    c.simulation.seed += 1
    assert c.config_hash != a.config_hash, "a seed change must change the hash"


def test_missing_config_dir_falls_back_to_defaults(tmp_path):
    settings = load_settings(tmp_path)
    assert settings.simulation.tick_rate_hz == 60
    assert settings.world.gravity_mps2 == pytest.approx(9.80665)


def test_reads_overrides_from_yaml(tmp_path):
    (tmp_path / "simulation.yaml").write_text(
        "simulation:\n  tick_rate_hz: 120\n  seed: 7\nworld:\n  gravity_mps2: 3.72\n",
        encoding="utf-8",
    )
    settings = load_settings(tmp_path)
    assert settings.simulation.tick_rate_hz == 120
    assert settings.simulation.dt == pytest.approx(1.0 / 120.0)
    assert settings.simulation.seed == 7
    assert settings.world.gravity_mps2 == pytest.approx(3.72)


def test_rejects_invalid_tick_rate():
    with pytest.raises(ValidationError):
        SimulationSettings(tick_rate_hz=0)


def test_rejects_default_speed_outside_allowed_set():
    with pytest.raises(ValidationError):
        SimulationSettings(allowed_speeds=[1.0, 2.0], default_speed=3.0)


def test_rejects_inverted_world_bounds():
    with pytest.raises(ValidationError):
        WorldBounds(x_min=100.0, x_max=-100.0)

    with pytest.raises(ValidationError):
        WorldBounds(altitude_min=5000.0, altitude_max=1000.0)


def test_rejects_malformed_yaml_document(tmp_path):
    (tmp_path / "simulation.yaml").write_text("- not\n- a mapping\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        load_settings(tmp_path)


def test_reward_weights_load_from_training_config(settings):
    weights = settings.training.reward_weights
    assert weights.mission == pytest.approx(5.0)
    assert weights.collision_penalty < 0, "penalties must be negative"
    assert weights.crash_penalty < 0, "losing the aircraft must cost, not pay"


def test_the_learnable_reward_terms_outweigh_the_constant_ones(settings):
    """PHASE 12: survival, coordination, information and smoothness are nearly
    constant for a competent policy. If their combined per-step weight rivals
    navigation's, the signal a policy can act on is drowned by one it cannot —
    which is exactly why the first trained policy scored worse than an
    untrained one."""
    weights = settings.training.reward_weights
    constant = weights.survival + weights.coordination + weights.information + weights.control_smoothness
    assert constant < weights.navigation
