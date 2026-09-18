"""Typed configuration loading for AIFCS.

Configuration lives in YAML under ``configs/`` and is never duplicated in code.
Every section is a Pydantic model, so a malformed config fails loudly at startup
instead of producing a subtly wrong simulation later.

A ``config_hash`` is derived from the fully-resolved settings: together with the
scenario seed it is what makes a run reproducible (see PHASE 41, deterministic
mode).
"""

from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

# Repository root: backend/core/config.py -> backend/core -> backend -> AIFCS
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_DIR = PROJECT_ROOT / "configs"

APP_NAME = "AIFCS"
APP_TITLE = "AI Flight Command & Simulation Platform"
APP_VERSION = "0.1.0"


class WorldBounds(BaseModel):
    """Abstract simulation volume, in metres, around the scenario datum."""

    x_min: float = -100_000.0
    x_max: float = 100_000.0
    y_min: float = -100_000.0
    y_max: float = 100_000.0
    altitude_min: float = 0.0
    altitude_max: float = 20_000.0

    @field_validator("x_max")
    @classmethod
    def _x_ordered(cls, v: float, info: Any) -> float:
        if "x_min" in info.data and v <= info.data["x_min"]:
            raise ValueError("x_max must be greater than x_min")
        return v

    @field_validator("y_max")
    @classmethod
    def _y_ordered(cls, v: float, info: Any) -> float:
        if "y_min" in info.data and v <= info.data["y_min"]:
            raise ValueError("y_max must be greater than y_min")
        return v

    @field_validator("altitude_max")
    @classmethod
    def _alt_ordered(cls, v: float, info: Any) -> float:
        if "altitude_min" in info.data and v <= info.data["altitude_min"]:
            raise ValueError("altitude_max must be greater than altitude_min")
        return v


class SimulationSettings(BaseModel):
    tick_rate_hz: int = Field(default=60, ge=1, le=1000)
    allowed_speeds: list[float] = Field(default=[0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 50.0])
    default_speed: float = Field(default=1.0, gt=0)
    max_duration_s: float = Field(default=3600.0, gt=0)
    deterministic: bool = True
    seed: int = 20260918

    @property
    def dt(self) -> float:
        """Fixed timestep, in simulation seconds."""
        return 1.0 / float(self.tick_rate_hz)

    @field_validator("allowed_speeds")
    @classmethod
    def _speeds_positive(cls, v: list[float]) -> list[float]:
        if not v:
            raise ValueError("allowed_speeds must not be empty")
        if any(s <= 0 for s in v):
            raise ValueError("allowed_speeds must all be positive")
        return sorted(v)

    @field_validator("default_speed")
    @classmethod
    def _default_speed_allowed(cls, v: float, info: Any) -> float:
        allowed = info.data.get("allowed_speeds")
        if allowed and v not in allowed:
            raise ValueError(f"default_speed {v} is not in allowed_speeds {allowed}")
        return v


class WorldSettings(BaseModel):
    bounds: WorldBounds = Field(default_factory=WorldBounds)
    gravity_mps2: float = Field(default=9.80665, gt=0)
    air_density_kgpm3: float = Field(default=1.225, gt=0)


class TelemetrySettings(BaseModel):
    broadcast_rate_hz: float = Field(default=20.0, gt=0, le=240.0)
    max_entities_per_frame: int = Field(default=64, ge=1)


class LoggingSettings(BaseModel):
    # ``json`` would shadow BaseModel.json, so the field is aliased instead.
    model_config = ConfigDict(populate_by_name=True)

    level: str = "INFO"
    json_format: bool = Field(default=True, alias="json")
    directory: str = "data/telemetry"

    @field_validator("level")
    @classmethod
    def _valid_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"log level must be one of {sorted(allowed)}")
        return upper


class AgentSettings(BaseModel):
    default_type: str = "rule"
    decision_rate_hz: float = Field(default=10.0, gt=0)
    strict_action_validation: bool = True


class ScenarioSettings(BaseModel):
    directory: str = "scenarios"
    default_scenario: str = "demo_alpha"
    strict_validation: bool = True


class RewardWeights(BaseModel):
    """Reward terms are weights, never hard-coded constants inside the engine."""

    survival: float = 1.0
    navigation: float = 1.0
    formation: float = 0.5
    mission: float = 2.0
    coordination: float = 0.5
    information: float = 0.25
    collision_penalty: float = -5.0
    energy_penalty: float = -0.1
    control_smoothness: float = 0.2


class TrainingSettings(BaseModel):
    device: str = "auto"
    output_directory: str = "models"
    log_directory: str = "data/training"
    reward_weights: RewardWeights = Field(default_factory=RewardWeights)

    @field_validator("device")
    @classmethod
    def _valid_device(cls, v: str) -> str:
        allowed = {"auto", "cpu", "cuda"}
        lower = v.lower()
        if lower not in allowed:
            raise ValueError(f"device must be one of {sorted(allowed)}")
        return lower


class Settings(BaseModel):
    """Fully-resolved AIFCS configuration."""

    app_name: str = APP_NAME
    app_title: str = APP_TITLE
    version: str = APP_VERSION
    simulation: SimulationSettings = Field(default_factory=SimulationSettings)
    world: WorldSettings = Field(default_factory=WorldSettings)
    telemetry: TelemetrySettings = Field(default_factory=TelemetrySettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    agents: AgentSettings = Field(default_factory=AgentSettings)
    scenarios: ScenarioSettings = Field(default_factory=ScenarioSettings)
    training: TrainingSettings = Field(default_factory=TrainingSettings)

    @property
    def config_hash(self) -> str:
        """Stable digest of the configuration, used to tag reproducible runs."""
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    @property
    def project_root(self) -> Path:
        return PROJECT_ROOT


def _read_yaml(path: Path) -> dict[str, Any]:
    """Read a YAML file, returning {} when it is absent or empty."""
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping at the top level")
    return data


def load_settings(config_dir: Path | str | None = None) -> Settings:
    """Load settings from ``config_dir`` (default ``configs/``).

    Missing files fall back to the defaults declared above, so the backend still
    starts on a fresh checkout. ``AIFCS_CONFIG_DIR`` overrides the directory.
    """
    if config_dir is None:
        config_dir = os.environ.get("AIFCS_CONFIG_DIR", DEFAULT_CONFIG_DIR)
    config_path = Path(config_dir)

    sim_doc = _read_yaml(config_path / "simulation.yaml")
    agents_doc = _read_yaml(config_path / "agents.yaml")
    scenarios_doc = _read_yaml(config_path / "scenarios.yaml")
    training_doc = _read_yaml(config_path / "training.yaml")

    training_section = dict(training_doc.get("training", {}))
    reward_section = training_doc.get("reward", {})
    if isinstance(reward_section, dict) and "weights" in reward_section:
        training_section["reward_weights"] = reward_section["weights"]

    return Settings(
        simulation=SimulationSettings(**sim_doc.get("simulation", {})),
        world=WorldSettings(**sim_doc.get("world", {})),
        telemetry=TelemetrySettings(**sim_doc.get("telemetry", {})),
        logging=LoggingSettings(**sim_doc.get("logging", {})),
        agents=AgentSettings(**agents_doc.get("agents", {})),
        scenarios=ScenarioSettings(**scenarios_doc.get("scenarios", {})),
        training=TrainingSettings(**training_section),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton (FastAPI dependency entry point)."""
    return load_settings()


def reset_settings_cache() -> None:
    """Drop the cached settings — used by tests and by config hot-reload."""
    get_settings.cache_clear()
