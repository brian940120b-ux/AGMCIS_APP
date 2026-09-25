"""Training and evaluation pipelines (PHASE 13).

PPO and SAC through Stable-Baselines3, with hyperparameters from
``configs/training.yaml``. Two things matter beyond "it trains":

* **A saved model records what it was trained against.** Alongside every
  ``.zip`` there is a card naming the scenario, the seed, the observation
  layout version and the reward weights. A policy is only meaningful against
  the environment that shaped it, and a model file alone does not say which
  that was.
* **Runs land in the database.** The PHASE 9 schema already declared
  ``training_runs`` and ``models``; this is what fills them, so training
  history is queryable the same way run history is.

The RL stack is an optional dependency. Importing this module without it
raises a message saying exactly what to install, rather than an ImportError
from three libraries down.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from core.config import AlgorithmSettings, Settings, get_settings
from core.logging_config import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from training.environment import AIFCSCombatEnv

log = get_logger("training.pipeline")

ALGORITHMS = ("ppo", "sac")

_INSTALL_HINT = (
    "The reinforcement-learning stack is not installed. Install it with:\n"
    "    .venv/bin/pip install -r requirements-ml.txt"
)


class TrainingUnavailableError(RuntimeError):
    """Raised when the RL dependencies are missing."""


def rl_available() -> bool:
    """Whether the whole RL stack can be imported.

    Gymnasium belongs in here with the other two. The environment needs it, and
    a check that passed without it would promise a training centre that cannot
    build an environment to train in.
    """
    try:
        import gymnasium  # noqa: F401
        import stable_baselines3  # noqa: F401
        import torch  # noqa: F401
    except ImportError:
        return False
    return True


def _require_rl() -> Any:
    try:
        import stable_baselines3 as sb3
    except ImportError as exc:  # pragma: no cover - exercised only without the stack
        raise TrainingUnavailableError(_INSTALL_HINT) from exc
    return sb3


def _require_env() -> type[AIFCSCombatEnv]:
    """The Gymnasium environment class, imported on use rather than on import.

    ``training.environment`` imports gymnasium at module scope, and this module
    is reachable from ``backend.main`` through the runtime. Importing it eagerly
    made an optional dependency mandatory: without the RL stack the API server
    did not start at all.
    """
    try:
        from training.environment import AIFCSCombatEnv
    except ImportError as exc:  # pragma: no cover - exercised only without the stack
        raise TrainingUnavailableError(_INSTALL_HINT) from exc
    return AIFCSCombatEnv


def _progress_bar_available() -> bool:
    """Whether SB3 can build its progress bar, which needs tqdm and rich."""
    try:
        import rich  # noqa: F401
        import tqdm  # noqa: F401
    except ImportError:
        return False
    return True


def resolve_device(requested: str) -> str:
    """Turn ``auto`` into the device actually available."""
    if requested != "auto":
        return requested
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def new_training_id(algorithm: str) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    return f"{algorithm}-{stamp}-{uuid.uuid4().hex[:4]}"


@dataclass
class TrainingResult:
    """What a finished training run produced."""

    training_id: str
    algorithm: str
    model_path: str
    card_path: str
    total_timesteps: int
    elapsed_s: float
    device: str
    scenario: str
    seed: int
    evaluation: dict[str, Any] | None = None
    card: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "training_id": self.training_id,
            "algorithm": self.algorithm,
            "model_path": self.model_path,
            "card_path": self.card_path,
            "total_timesteps": self.total_timesteps,
            "elapsed_s": round(self.elapsed_s, 2),
            "device": self.device,
            "scenario": self.scenario,
            "seed": self.seed,
            "evaluation": self.evaluation,
        }


class TrainingPipeline:
    """Builds environments, trains policies, evaluates and stores them."""

    def __init__(self, settings: Settings | None = None, repository: Any | None = None) -> None:
        self.settings = settings or get_settings()
        # Optional: when present, runs and models are recorded in the PHASE 9
        # database. Training works without it; the history is just not kept.
        self.repository = repository

    # ----------------------------------------------------------- environment

    def make_env(self, *, seed: int | None = None, max_episode_seconds: float | None = None) -> Any:
        """One environment, wrapped in SB3's Monitor for episode statistics."""
        from stable_baselines3.common.monitor import Monitor

        training = self.settings.training
        env = _require_env()(
            scenario_name=training.scenario,
            entity_id=training.entity_id,
            settings=self.settings,
            max_episode_seconds=max_episode_seconds or training.max_episode_seconds,
            seed=seed if seed is not None else self.settings.simulation.seed,
        )
        return Monitor(env)

    def algorithm_settings(self, algorithm: str) -> AlgorithmSettings:
        if algorithm not in ALGORITHMS:
            raise ValueError(f"unknown algorithm {algorithm!r}; choose one of {list(ALGORITHMS)}")
        return getattr(self.settings.training, algorithm)

    # -------------------------------------------------------------- training

    def train(
        self,
        algorithm: str = "ppo",
        *,
        total_timesteps: int | None = None,
        seed: int | None = None,
        evaluate_episodes: int = 0,
        progress: bool = False,
        callback: Any | None = None,
    ) -> TrainingResult:
        """Train a policy and save it with its card.

        ``callback`` is handed straight to Stable-Baselines3. A job runner uses
        it to watch progress and to stop cleanly: an SB3 callback that returns
        False ends ``learn`` at the next step boundary, which leaves the model
        in a state worth saving rather than killing a thread mid-update.
        """
        sb3 = _require_rl()
        hyper = self.algorithm_settings(algorithm)
        steps = total_timesteps or hyper.total_timesteps
        seed = seed if seed is not None else self.settings.simulation.seed
        device = resolve_device(self.settings.training.device)

        training_id = new_training_id(algorithm)
        env = self.make_env(seed=seed)

        kwargs: dict[str, Any] = {
            "policy": "MlpPolicy",
            "env": env,
            "learning_rate": hyper.learning_rate,
            "gamma": hyper.gamma,
            "batch_size": hyper.batch_size,
            "seed": seed,
            "device": device,
            "verbose": 0,
        }
        if algorithm == "ppo":
            kwargs["n_steps"] = hyper.n_steps or 2048
            model = sb3.PPO(**kwargs)
        else:
            model = sb3.SAC(**kwargs)

        self._record_start(training_id, algorithm, seed, steps)
        log.info(
            "training started",
            extra={
                "event": "TRAINING_STARTED",
                "training_id": training_id,
                "algorithm": algorithm,
                "timesteps": steps,
                "device": device,
                "scenario": self.settings.training.scenario,
            },
        )

        # SB3's progress bar needs tqdm and rich. They are listed in
        # requirements-ml.txt, but an install that predates that listing would
        # otherwise lose the whole run to a missing cosmetic: `model.learn`
        # raises ImportError before it takes a single step.
        if progress and not _progress_bar_available():
            log.warning(
                "training progress bar unavailable — install tqdm and rich for it",
                extra={"event": "TRAINING_PROGRESS_BAR_UNAVAILABLE", "training_id": training_id},
            )
            progress = False

        started = time.perf_counter()
        try:
            model.learn(total_timesteps=steps, progress_bar=progress, callback=callback)
        finally:
            elapsed = time.perf_counter() - started

        # What the model was actually trained for, not what was asked for. A
        # cancelled job stops early and SB3 overshoots a little by stepping in
        # blocks, so the requested figure is the one number that is never right.
        trained_steps = int(getattr(model, "num_timesteps", steps) or steps)

        model_path, card_path, card = self._save(
            model, training_id, algorithm, seed, trained_steps, device, elapsed
        )

        evaluation = None
        if evaluate_episodes > 0:
            evaluation = self.evaluate(model, episodes=evaluate_episodes, seed=seed + 10_000)
            card["evaluation"] = evaluation
            Path(card_path).write_text(json.dumps(card, indent=2), encoding="utf-8")

        env.close()
        self._record_finish(training_id, trained_steps, model_path, algorithm)
        log.info(
            "training finished",
            extra={
                "event": "TRAINING_COMPLETED",
                "training_id": training_id,
                "elapsed_s": round(elapsed, 2),
                "timesteps": trained_steps,
                "model": model_path,
            },
        )

        return TrainingResult(
            training_id=training_id,
            algorithm=algorithm,
            model_path=model_path,
            card_path=card_path,
            total_timesteps=trained_steps,
            elapsed_s=elapsed,
            device=device,
            scenario=self.settings.training.scenario,
            seed=seed,
            evaluation=evaluation,
            card=card,
        )

    # ------------------------------------------------------------ evaluation

    def evaluate(
        self,
        model: Any,
        *,
        episodes: int = 5,
        seed: int | None = None,
        deterministic: bool = True,
        on_episode: Callable[[int, int, float], bool] | None = None,
    ) -> dict[str, Any]:
        """Run finished episodes and report what the policy actually did.

        Deliberately not just a mean reward: a policy that scores well by
        circling and one that scores well by flying the route are very
        different, and only the extra fields tell them apart.

        ``on_episode`` is called after each finished episode with the number
        done, the number asked for and the running mean reward. Returning False
        stops early — an episode is the smallest unit that can be stopped
        without reporting a partial one as if it had finished. The result then
        says how many episodes actually ran and that it was cut short, because
        a mean over two episodes is not a mean over five.
        """
        env = self.make_env(seed=seed)
        rewards: list[float] = []
        lengths: list[int] = []
        goals: list[int] = []
        endings: dict[str, int] = {}
        cancelled = False

        for episode in range(episodes):
            observation, _ = env.reset(seed=(seed or 0) + episode)
            total, steps = 0.0, 0
            terminated = truncated = False
            info: dict[str, Any] = {}
            while not (terminated or truncated):
                action, _ = model.predict(observation, deterministic=deterministic)
                observation, reward, terminated, truncated, info = env.step(action)
                total += float(reward)
                steps += 1
            rewards.append(total)
            lengths.append(steps)
            goals.append(int(info.get("goals_reached", 0)))
            ending = str(info.get("terminated_reason") or "time_limit")
            endings[ending] = endings.get(ending, 0) + 1

            if on_episode is not None and not on_episode(episode + 1, episodes, float(np.mean(rewards))):
                cancelled = True
                break

        env.close()
        return {
            "episodes": len(rewards),
            "episodes_requested": episodes,
            "cancelled": cancelled,
            "deterministic": deterministic,
            "mean_reward": round(float(np.mean(rewards)), 3),
            "std_reward": round(float(np.std(rewards)), 3),
            "min_reward": round(float(np.min(rewards)), 3),
            "max_reward": round(float(np.max(rewards)), 3),
            "mean_episode_steps": round(float(np.mean(lengths)), 1),
            "mean_goals_reached": round(float(np.mean(goals)), 2),
            "endings": endings,
        }

    def evaluate_saved(
        self,
        model_path: str | Path,
        *,
        episodes: int = 5,
        seed: int | None = None,
        on_episode: Callable[[int, int, float], bool] | None = None,
    ) -> dict[str, Any]:
        model, _ = self.load(model_path)
        return self.evaluate(model, episodes=episodes, seed=seed, on_episode=on_episode)

    # ------------------------------------------------------- saving, loading

    def _model_directory(self) -> Path:
        directory = Path(self.settings.training.output_directory)
        if not directory.is_absolute():
            directory = self.settings.project_root / directory
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _save(
        self,
        model: Any,
        training_id: str,
        algorithm: str,
        seed: int,
        steps: int,
        device: str,
        elapsed: float,
    ) -> tuple[str, str, dict[str, Any]]:
        directory = self._model_directory()
        model_path = directory / f"{training_id}.zip"
        card_path = directory / f"{training_id}.json"
        model.save(model_path)

        # The card is what makes the model meaningful later. A .zip on its own
        # does not say which environment shaped it, and a policy run against a
        # different observation layout is silently wrong rather than broken.
        spec = _require_env()(
            scenario_name=self.settings.training.scenario,
            entity_id=self.settings.training.entity_id,
            settings=self.settings,
            max_episode_seconds=self.settings.training.max_episode_seconds,
        ).spec_summary()

        card = {
            "training_id": training_id,
            "algorithm": algorithm,
            "created_at": time.time(),
            "app_version": self.settings.version,
            "config_hash": self.settings.config_hash,
            "seed": seed,
            "total_timesteps": steps,
            "elapsed_s": round(elapsed, 2),
            "device": device,
            "hyperparameters": self.algorithm_settings(algorithm).model_dump(),
            "environment": spec,
            "notice": (
                "Fictional research policy. Trained on flight and navigation only; "
                "this platform models no weapon, engagement or targeting capability."
            ),
        }
        card_path.write_text(json.dumps(card, indent=2), encoding="utf-8")
        return str(model_path), str(card_path), card

    def load(self, model_path: str | Path) -> tuple[Any, dict[str, Any] | None]:
        """Load a saved policy and its card, warning if the layout has moved on."""
        sb3 = _require_rl()
        path = Path(model_path)
        if not path.is_file():
            raise FileNotFoundError(f"no such model: {path}")

        algorithm = path.stem.split("-")[0]
        loader = sb3.SAC if algorithm == "sac" else sb3.PPO
        model = loader.load(path, device=resolve_device(self.settings.training.device))

        card = None
        card_path = path.with_suffix(".json")
        if card_path.is_file():
            card = json.loads(card_path.read_text(encoding="utf-8"))
            from training.observation_encoder import LAYOUT_VERSION

            trained_layout = card.get("environment", {}).get("observation_layout_version")
            if trained_layout is not None and trained_layout != LAYOUT_VERSION:
                # Not an error: the model still loads. But its inputs no longer
                # mean what they meant, and that must not pass silently.
                log.warning(
                    "model was trained against a different observation layout",
                    extra={
                        "event": "MODEL_LAYOUT_MISMATCH",
                        "model": str(path),
                        "trained_layout": trained_layout,
                        "current_layout": LAYOUT_VERSION,
                    },
                )
        return model, card

    def list_models(self) -> list[dict[str, Any]]:
        """Every saved policy, newest first, with its card where there is one."""
        directory = self._model_directory()
        out: list[dict[str, Any]] = []
        for path in sorted(directory.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True):
            entry: dict[str, Any] = {
                "model_id": path.stem,
                "path": str(path),
                "size_bytes": path.stat().st_size,
                "created_at": path.stat().st_mtime,
                "algorithm": path.stem.split("-")[0],
                "card": None,
            }
            card_path = path.with_suffix(".json")
            if card_path.is_file():
                try:
                    entry["card"] = json.loads(card_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    entry["card_error"] = str(exc)
            out.append(entry)
        return out

    # --------------------------------------------------------- run recording

    def _record_start(self, training_id: str, algorithm: str, seed: int, steps: int) -> None:
        if self.repository is None:
            return
        try:
            with self.repository.db.transaction() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO training_runs
                        (training_id, algorithm, scenario_name, seed, config_hash,
                         started_at, status, total_steps, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        training_id,
                        algorithm,
                        self.settings.training.scenario,
                        seed,
                        self.settings.config_hash,
                        time.time(),
                        "RUNNING",
                        steps,
                        "",
                    ),
                )
        except Exception:
            # Losing the history entry must not lose the training run.
            log.exception("could not record training start", extra={"event": "TRAINING_RECORD_FAILED"})

    def record_status(self, training_id: str, status: str, notes: str = "") -> None:
        """Mark a training run's outcome when it did not complete.

        A run can now be cancelled from the dashboard, or be left behind by a
        server restart. Leaving either as RUNNING for ever would make the
        history claim a job is still going when nothing is.
        """
        if self.repository is None:
            return
        try:
            with self.repository.db.transaction() as conn:
                conn.execute(
                    "UPDATE training_runs SET ended_at = ?, status = ?, notes = ? WHERE training_id = ?",
                    (time.time(), status, notes, training_id),
                )
        except Exception:
            log.exception("could not record training status", extra={"event": "TRAINING_RECORD_FAILED"})

    def reconcile_interrupted(self) -> int:
        """Close out runs the last process was in the middle of.

        Training happens in this process. If it stops — a restart, a crash —
        every RUNNING row is stale by definition, because a job cannot outlive
        the server that was running it.
        """
        if self.repository is None:
            return 0
        try:
            with self.repository.db.transaction() as conn:
                cursor = conn.execute(
                    "UPDATE training_runs SET ended_at = ?, status = ?, notes = ? WHERE status = ?",
                    (time.time(), "INTERRUPTED", "the server stopped while this was training", "RUNNING"),
                )
                return int(cursor.rowcount or 0)
        except Exception:
            log.exception("could not reconcile training runs", extra={"event": "TRAINING_RECORD_FAILED"})
            return 0

    def _record_finish(self, training_id: str, steps: int, model_path: str, algorithm: str) -> None:
        if self.repository is None:
            return
        try:
            with self.repository.db.transaction() as conn:
                conn.execute(
                    """
                    UPDATE training_runs
                       SET ended_at = ?, status = ?, total_steps = ?
                     WHERE training_id = ?
                    """,
                    (time.time(), "COMPLETED", steps, training_id),
                )
                conn.execute(
                    """
                    INSERT OR REPLACE INTO models
                        (model_id, training_id, algorithm, path, created_at, notes)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (training_id, training_id, algorithm, model_path, time.time(), ""),
                )
        except Exception:
            log.exception("could not record training finish", extra={"event": "TRAINING_RECORD_FAILED"})
