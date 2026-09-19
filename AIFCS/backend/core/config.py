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


class SensorSettings(BaseModel):
    """Perception limits applied between truth and an agent (PHASE 5)."""

    enabled: bool = True
    max_range_m: float = Field(default=80_000.0, gt=0)
    field_of_regard_deg: float = Field(default=120.0, gt=0, le=180.0)
    latency_s: float = Field(default=0.2, ge=0.0, le=5.0)
    dropout_probability: float = Field(default=0.05, ge=0.0, le=1.0)
    track_memory_s: float = Field(default=3.0, ge=0.0)
    position_noise_base_m: float = Field(default=20.0, ge=0.0)
    position_noise_per_km_m: float = Field(default=2.0, ge=0.0)
    velocity_noise_mps: float = Field(default=3.0, ge=0.0)
    ownship_position_noise_m: float = Field(default=5.0, ge=0.0)
    ownship_velocity_noise_mps: float = Field(default=0.5, ge=0.0)


class CommunicationSettings(BaseModel):
    """Abstract datalink performance (PHASE 6).

    A simulation transport between simulated units. It does no real networking.
    """

    enabled: bool = True
    latency_base_s: float = Field(default=0.15, ge=0.0, le=10.0)
    latency_jitter_s: float = Field(default=0.08, ge=0.0, le=5.0)
    packet_loss_probability: float = Field(default=0.03, ge=0.0, le=1.0)
    max_messages_per_second: float = Field(default=20.0, gt=0)
    report_rate_hz: float = Field(default=4.0, gt=0)
    blackout_windows: list[list[float]] = Field(default_factory=list)

    @field_validator("blackout_windows")
    @classmethod
    def _windows_are_ordered_pairs(cls, v: list[list[float]]) -> list[list[float]]:
        for window in v:
            if len(window) != 2:
                raise ValueError("each blackout window must be a [start, end] pair")
            if window[1] <= window[0]:
                raise ValueError(f"blackout window {window} must end after it starts")
        return v


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


class RuleAgentSettings(BaseModel):
    """Behaviour tuning for the rule-based pilot (PHASE 3)."""

    waypoint_capture_radius_m: float = Field(default=800.0, gt=0)
    formation_spacing_m: float = Field(default=850.0, gt=0)
    formation_station_tolerance_m: float = Field(default=150.0, gt=0)
    collision_radius_m: float = Field(default=200.0, gt=0)
    cruise_speed_mps: float = Field(default=220.0, gt=0)
    max_bank_deg: float = Field(default=60.0, gt=0, le=89.0)

    @field_validator("collision_radius_m")
    @classmethod
    def _collision_below_formation(cls, v: float, info: Any) -> float:
        spacing = info.data.get("formation_spacing_m")
        if spacing is not None and v >= spacing:
            raise ValueError(
                "collision_radius_m must be smaller than formation_spacing_m, "
                "or formation partners trigger collision avoidance against each other"
            )
        return v


class SafetySettings(BaseModel):
    """Guard-rails enforced by the flight controller (PHASE 4)."""

    max_control_rate_per_s: float = Field(default=4.0, gt=0)
    max_load_factor: float = Field(default=9.0, gt=0)
    min_altitude_m: float = Field(default=100.0, ge=0)
    max_altitude_m: float = Field(default=19_000.0, gt=0)
    altitude_buffer_m: float = Field(default=500.0, ge=0)
    reject_on_invalid_state: bool = True

    @field_validator("max_altitude_m")
    @classmethod
    def _band_is_ordered(cls, v: float, info: Any) -> float:
        floor = info.data.get("min_altitude_m")
        if floor is not None and v <= floor:
            raise ValueError("max_altitude_m must be above min_altitude_m")
        return v


class AgentSettings(BaseModel):
    default_type: str = "rule"
    decision_rate_hz: float = Field(default=10.0, gt=0)
    strict_action_validation: bool = True
    rule_agent: RuleAgentSettings = Field(default_factory=RuleAgentSettings)

    @field_validator("default_type")
    @classmethod
    def _known_agent_type(cls, v: str) -> str:
        allowed = {"rule", "none"}
        if v not in allowed:
            raise ValueError(f"default_type must be one of {sorted(allowed)}")
        return v


class ScenarioSettings(BaseModel):
    directory: str = "scenarios"
    default_scenario: str = "demo_alpha"
    strict_validation: bool = True


class ReplaySettings(BaseModel):
    """Recording of a run for later playback and analysis (PHASE 9)."""

    enabled: bool = True
    directory: str = "data/replay"
    record_rate_hz: float = 20.0
    compress: bool = True
    max_recordings_kept: int = 50
    max_file_mb: float = 200.0

    @field_validator("record_rate_hz")
    @classmethod
    def _positive_rate(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("record_rate_hz must be positive")
        return v

    @field_validator("max_recordings_kept")
    @classmethod
    def _not_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("max_recordings_kept must be 0 (no pruning) or more")
        return v

    @field_validator("max_file_mb")
    @classmethod
    def _positive_size(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("max_file_mb must be positive")
        return v


class ScoreWeights(BaseModel):
    """Weights for the scoring terms.

    Flight- and mission-quality only. There is no weapon, engagement or
    targeting term because AIFCS models none, and scoring one would imply a
    capability the platform does not have.
    """

    survival: float = 30.0
    navigation: float = 25.0
    formation: float = 15.0
    safety: float = 15.0
    efficiency: float = 10.0
    information: float = 5.0

    @field_validator("*")
    @classmethod
    def _not_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("score weights must not be negative")
        return v

    def total(self) -> float:
        return (
            self.survival
            + self.navigation
            + self.formation
            + self.safety
            + self.efficiency
            + self.information
        )


class ScoreThresholds(BaseModel):
    """The measured value at which each term scores zero.

    Terms are scored as ``1 - measured / threshold``, clamped to 0..1, so every
    standard a run is judged against lives in YAML rather than in the code.
    """

    heading_tolerance_deg: float = 45.0
    altitude_tolerance_m: float = 300.0
    seconds_per_expected_waypoint: float = 150.0
    formation_tolerance_m: float = 300.0
    formation_settle_fraction: float = 0.5
    rejected_commands_for_zero: int = 5
    envelope_intervention_fraction_for_zero: float = 0.2
    boundary_events_for_zero: int = 3
    control_rate_for_zero: float = 2.0
    track_age_tolerance_s: float = 3.0

    @field_validator("*")
    @classmethod
    def _must_be_positive(cls, v: float) -> float:
        # Each threshold is a divisor; zero would make the term undefined.
        if v <= 0:
            raise ValueError("scoring thresholds must be greater than zero")
        return v

    @field_validator("formation_settle_fraction")
    @classmethod
    def _is_a_fraction(cls, v: float) -> float:
        if v > 1.0:
            raise ValueError("formation_settle_fraction is a fraction of the run, so at most 1.0")
        return v


class ScoringSettings(BaseModel):
    enabled: bool = True
    weights: ScoreWeights = Field(default_factory=ScoreWeights)
    redistribute_inapplicable: bool = True
    thresholds: ScoreThresholds = Field(default_factory=ScoreThresholds)

    @field_validator("weights")
    @classmethod
    def _weights_sum_to_something(cls, v: ScoreWeights) -> ScoreWeights:
        if v.total() <= 0:
            raise ValueError("at least one scoring weight must be greater than zero")
        return v


class StorageSettings(BaseModel):
    enabled: bool = True
    database_path: str = "data/aifcs.db"
    max_decisions_per_run: int = 5000

    @field_validator("max_decisions_per_run")
    @classmethod
    def _not_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("max_decisions_per_run must be 0 (store none) or more")
        return v


class RewardWeights(BaseModel):
    """Reward terms are weights, never hard-coded constants inside the engine.

    The per-step terms are deliberately small. Measuring a 400-step episode
    showed survival, coordination, information and smoothness contributing a
    near-constant 760 of ~780 total reward while navigation — the only term the
    policy could actually change — moved by +/-40. A policy cannot learn from
    5% signal, so the terms it cannot influence are worth little per step and
    the ones it can are worth a lot.
    """

    survival: float = 0.05
    navigation: float = 1.0
    formation: float = 0.5
    mission: float = 5.0
    coordination: float = 0.05
    information: float = 0.05
    # Terminal, not per-step: paid once if the episode ends in a crash, leaving
    # the world, or being disabled.
    crash_penalty: float = -50.0
    collision_penalty: float = -5.0
    energy_penalty: float = -0.1
    control_smoothness: float = 0.05


class AlgorithmSettings(BaseModel):
    """Hyperparameters for one algorithm. Tuning lives in YAML, never in code."""

    total_timesteps: int = 200_000
    learning_rate: float = 3e-4
    gamma: float = 0.99
    batch_size: int = 64
    n_steps: int | None = None  # PPO only; SAC has no rollout length

    @field_validator("total_timesteps", "batch_size")
    @classmethod
    def _positive_int(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("must be greater than zero")
        return v

    @field_validator("learning_rate")
    @classmethod
    def _positive_rate(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("learning_rate must be greater than zero")
        return v

    @field_validator("gamma")
    @classmethod
    def _is_a_discount(cls, v: float) -> float:
        if not 0.0 < v <= 1.0:
            raise ValueError("gamma must be in (0, 1]")
        return v


class TrainingSettings(BaseModel):
    device: str = "auto"
    output_directory: str = "models"
    log_directory: str = "data/training"
    reward_weights: RewardWeights = Field(default_factory=RewardWeights)
    ppo: AlgorithmSettings = Field(default_factory=AlgorithmSettings)
    sac: AlgorithmSettings = Field(default_factory=lambda: AlgorithmSettings(batch_size=256))
    # The task the environment trains on. demo_alpha's legs are three minutes
    # long, so an episode would end before a single waypoint was reached.
    scenario: str = "training_navigation"
    entity_id: str | None = None
    max_episode_seconds: float = 120.0

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
    sensors: SensorSettings = Field(default_factory=SensorSettings)
    communications: CommunicationSettings = Field(default_factory=CommunicationSettings)
    telemetry: TelemetrySettings = Field(default_factory=TelemetrySettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    agents: AgentSettings = Field(default_factory=AgentSettings)
    safety: SafetySettings = Field(default_factory=SafetySettings)
    scenarios: ScenarioSettings = Field(default_factory=ScenarioSettings)
    training: TrainingSettings = Field(default_factory=TrainingSettings)
    replay: ReplaySettings = Field(default_factory=ReplaySettings)
    scoring: ScoringSettings = Field(default_factory=ScoringSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)

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
    analysis_doc = _read_yaml(config_path / "analysis.yaml")

    training_section = dict(training_doc.get("training", {}))
    reward_section = training_doc.get("reward", {})
    if isinstance(reward_section, dict) and "weights" in reward_section:
        training_section["reward_weights"] = reward_section["weights"]
    # ppo: and sac: are top-level sections in training.yaml, not nested under
    # training:, because that is how they read.
    for algorithm in ("ppo", "sac"):
        if algorithm in training_doc:
            training_section[algorithm] = training_doc[algorithm]

    agents_section = dict(agents_doc.get("agents", {}))
    if "rule_agent" in agents_doc:
        agents_section["rule_agent"] = agents_doc["rule_agent"]

    return Settings(
        simulation=SimulationSettings(**sim_doc.get("simulation", {})),
        world=WorldSettings(**sim_doc.get("world", {})),
        sensors=SensorSettings(**sim_doc.get("sensors", {})),
        communications=CommunicationSettings(**sim_doc.get("communications", {})),
        telemetry=TelemetrySettings(**sim_doc.get("telemetry", {})),
        logging=LoggingSettings(**sim_doc.get("logging", {})),
        agents=AgentSettings(**agents_section),
        safety=SafetySettings(**agents_doc.get("safety", {})),
        scenarios=ScenarioSettings(**scenarios_doc.get("scenarios", {})),
        training=TrainingSettings(**training_section),
        replay=ReplaySettings(**analysis_doc.get("replay", {})),
        scoring=ScoringSettings(**analysis_doc.get("scoring", {})),
        storage=StorageSettings(**analysis_doc.get("storage", {})),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton (FastAPI dependency entry point)."""
    return load_settings()


def reset_settings_cache() -> None:
    """Drop the cached settings — used by tests and by config hot-reload."""
    get_settings.cache_clear()
