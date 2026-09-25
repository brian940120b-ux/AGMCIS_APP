"""Running a training job from the dashboard (PHASE 18).

``train.py`` said it plainly: driving a long job from the browser needs job
management — progress, cancellation, surviving a reload — and until those
existed the dashboard said training was run from the command line rather than
offering a button that could not do them. This is those three things.

**Progress.** An SB3 callback reports timesteps and the running mean episode
reward, sampled rather than recorded per step, so a long job's history stays a
curve instead of a million points.

**Cancellation.** The callback returns False, which ends ``learn`` at the next
step boundary. Nothing is killed mid-update: the model is saved with however
many steps it actually got, and the run is recorded as CANCELLED rather than
quietly filed as a completed one that happens to be short.

**Surviving a reload.** The job lives in the server, not the page, so a browser
reload rejoins it. Surviving a *server* restart is a different claim and this
does not make it — a job cannot outlive the process running it — so every run
left RUNNING by a restart is reconciled to INTERRUPTED at startup instead of
being left to look like it is still going.

One job at a time, on purpose. Two would contend for the same cores and both
would report times that meant nothing.

Nothing here touches the live simulation. The environment builds its own engine,
exactly as the command line does.
"""

from __future__ import annotations

import itertools
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from core.config import Settings, get_settings
from core.logging_config import get_logger
from training.pipeline import ALGORITHMS, TrainingPipeline, TrainingUnavailableError, rl_available

log = get_logger("training.jobs")

# Job ids carry a counter as well as a clock. PHASE 15 learned this the hard
# way: a millisecond timestamp is not unique when two ids are minted close
# together, and duplicated ids silently overwrite each other's records.
_job_counter = itertools.count(1)

# Terminal states. A job in one of these is finished with, whatever the outcome.
FINISHED = frozenset({"COMPLETED", "CANCELLED", "FAILED"})


class TrainingBusyError(RuntimeError):
    """Raised when a job is asked for while one is already running."""


class TrainingRefusedError(RuntimeError):
    """Raised when a job must not start — bad request, or a simulation running."""


@dataclass
class MetricPoint:
    """One sample of how training is going."""

    timesteps: int
    elapsed_s: float
    episode_reward_mean: float | None = None
    episode_length_mean: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "timesteps": self.timesteps,
            "elapsed_s": round(self.elapsed_s, 2),
            "episode_reward_mean": self.episode_reward_mean,
            "episode_length_mean": self.episode_length_mean,
        }


@dataclass
class TrainingJob:
    """One background job, as the dashboard sees it.

    Training and evaluation share this because they share the machine: both
    saturate the same cores, so allowing one of each at once would make both
    of their timings meaningless. ``kind`` says which this is.
    """

    job_id: str
    algorithm: str
    requested_timesteps: int
    seed: int
    evaluate_episodes: int
    kind: str = "TRAIN"
    model_id: str | None = None
    state: str = "PENDING"
    timesteps: int = 0
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    metrics: list[MetricPoint] = field(default_factory=list)
    error: str | None = None
    result: dict[str, Any] | None = None
    cancel_requested: bool = False

    @property
    def fraction(self) -> float:
        """Progress, clamped for a progress bar.

        PPO collects in blocks of ``n_steps`` and cannot stop part way through
        one, so a job routinely finishes a little past what was asked for. The
        bar is clamped; ``timesteps`` is not, and it is the figure the card and
        the history record.
        """
        if self.kind == "EVALUATE":
            if self.evaluate_episodes <= 0:
                return 0.0
            return min(1.0, self.timesteps / self.evaluate_episodes)
        if self.requested_timesteps <= 0:
            return 0.0
        return min(1.0, self.timesteps / self.requested_timesteps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "model_id": self.model_id,
            "algorithm": self.algorithm,
            "requested_timesteps": self.requested_timesteps,
            "timesteps": self.timesteps,
            "fraction": round(self.fraction, 4),
            "seed": self.seed,
            "evaluate_episodes": self.evaluate_episodes,
            "state": self.state,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "elapsed_s": round((self.ended_at or time.time()) - self.started_at, 2),
            "cancel_requested": self.cancel_requested,
            "metrics": [m.to_dict() for m in self.metrics],
            "error": self.error,
            "result": self.result,
            "finished": self.state in FINISHED,
        }


# Most points a job's curve is allowed to hold. Past it the series is thinned
# rather than truncated, so the shape of a long run survives and the newest
# progress is never the part that gets dropped.
MAX_METRIC_POINTS = 400


def _progress_callback(job: TrainingJob, lock: threading.Lock, every: int) -> Any:
    """An SB3 callback that reports progress and honours a cancellation.

    Built here rather than at import time because ``BaseCallback`` only exists
    when the RL stack is installed, and this module must import without it.
    """
    from stable_baselines3.common.callbacks import BaseCallback

    class _Progress(BaseCallback):  # type: ignore[misc]
        def __init__(self) -> None:
            super().__init__(verbose=0)
            # Set when learning actually begins. Built here, the clock would
            # start before the environment exists and the first sample would
            # carry the setup time as if it were training time.
            self._started = time.perf_counter()
            self._next_sample = 0
            self._every = max(1, every)

        def _on_training_start(self) -> None:
            self._started = time.perf_counter()

        def _on_step(self) -> bool:
            steps = int(self.num_timesteps)
            with lock:
                job.timesteps = steps
                if steps >= self._next_sample:
                    self._next_sample = steps + self._every
                    job.metrics.append(
                        MetricPoint(
                            timesteps=steps,
                            elapsed_s=time.perf_counter() - self._started,
                            episode_reward_mean=self._mean("r"),
                            episode_length_mean=self._mean("l"),
                        )
                    )
                    # The interval was set from what was *asked* for, and PPO
                    # collects in whole blocks, so a short request can overshoot
                    # far enough to blow past the point budget. Halve the
                    # resolution when it does, rather than stopping recording.
                    if len(job.metrics) > MAX_METRIC_POINTS:
                        job.metrics = job.metrics[::2]
                        self._every *= 2
                cancelled = job.cancel_requested
            # Returning False ends learn() at this step boundary, leaving a
            # model worth saving rather than a thread killed mid-update.
            return not cancelled

        def _mean(self, key: str) -> float | None:
            buffer = getattr(self.model, "ep_info_buffer", None)
            if not buffer:
                return None
            values = [float(info[key]) for info in buffer if key in info]
            return round(sum(values) / len(values), 4) if values else None

    return _Progress()


class TrainingJobRunner:
    """Owns the one training job that may be running, and the ones that were."""

    def __init__(self, settings: Settings | None = None, repository: Any | None = None) -> None:
        self.settings = settings or get_settings()
        self.repository = repository
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._current: TrainingJob | None = None
        self._history: list[TrainingJob] = []
        # Set by the API so a job cannot be started on top of a live run: both
        # would fight for the same cores and neither's timings would mean
        # anything. A callable rather than an engine reference, so this module
        # stays clear of the simulation.
        self.simulation_is_running: Callable[[], bool] = lambda: False

    # ------------------------------------------------------------- inspection

    def current(self) -> TrainingJob | None:
        with self._lock:
            return self._current

    def get(self, job_id: str) -> TrainingJob | None:
        with self._lock:
            if self._current is not None and self._current.job_id == job_id:
                return self._current
            return next((j for j in self._history if j.job_id == job_id), None)

    def history(self, limit: int = 20) -> list[TrainingJob]:
        with self._lock:
            return list(reversed(self._history))[:limit]

    def busy(self) -> bool:
        with self._lock:
            return self._current is not None and self._current.state not in FINISHED

    def status(self) -> dict[str, Any]:
        current = self.current()
        return {
            "available": rl_available(),
            "busy": self.busy(),
            "current": current.to_dict() if current else None,
            "history": [j.to_dict() for j in self.history()],
            "max_timesteps": self.settings.training.max_timesteps_per_job,
            "max_evaluation_episodes": self.settings.training.max_evaluation_episodes,
            "algorithms": list(ALGORITHMS),
            "notice": (
                "Training runs in this server process. One job at a time, and a job "
                "does not survive a restart."
            ),
        }

    # ---------------------------------------------------------------- control

    def start(
        self,
        algorithm: str = "ppo",
        *,
        timesteps: int | None = None,
        seed: int | None = None,
        evaluate_episodes: int = 0,
    ) -> TrainingJob:
        """Begin a training job, or explain why it cannot begin."""
        if not rl_available():
            raise TrainingUnavailableError(
                "The reinforcement-learning stack is not installed. Install it with:\n"
                "    pip install -r requirements-ml.txt"
            )
        if algorithm not in ALGORITHMS:
            raise TrainingRefusedError(f"algorithm must be one of {sorted(ALGORITHMS)}")
        if self.simulation_is_running():
            raise TrainingRefusedError(
                "a simulation is running — training would compete with it for the same "
                "cores and both would slow down. Stop the simulation first."
            )

        cap = self.settings.training.max_timesteps_per_job
        # `is None` rather than falsy: a request for zero steps is a mistake to
        # refuse, not an omission to fill in with the configured budget. And the
        # budget is the one belonging to the algorithm being asked for.
        if timesteps is None:
            configured = getattr(self.settings.training, algorithm, self.settings.training.ppo)
            requested = configured.total_timesteps
        else:
            requested = timesteps
        if requested < 1:
            raise TrainingRefusedError("timesteps must be at least 1")
        if requested > cap:
            raise TrainingRefusedError(
                f"{requested} timesteps is above the {cap} cap in configs/training.yaml"
            )

        with self._lock:
            if self._current is not None and self._current.state not in FINISHED:
                raise TrainingBusyError(
                    f"training job {self._current.job_id} is already running — "
                    "stop it before starting another"
                )
            job = TrainingJob(
                job_id=f"JOB-{int(time.time() * 1000) % 100_000_000:08d}-{next(_job_counter):03d}",
                algorithm=algorithm,
                requested_timesteps=requested,
                seed=seed if seed is not None else self.settings.simulation.seed,
                evaluate_episodes=max(0, evaluate_episodes),
            )
            self._current = job
            self._thread = threading.Thread(
                target=self._run, args=(job,), name=f"training-{job.job_id}", daemon=True
            )
            self._thread.start()

        log.info(
            "training job started",
            extra={
                "event": "TRAINING_JOB_STARTED",
                "job_id": job.job_id,
                "algorithm": algorithm,
                "timesteps": requested,
            },
        )
        return job

    def start_evaluation(
        self,
        model_id: str,
        *,
        episodes: int = 5,
        seed: int | None = None,
    ) -> TrainingJob:
        """Measure a saved policy against the current environment.

        Refused for a policy whose observation layout has moved on. That is not
        caution: such a policy loads cleanly and produces actions, from numbers
        that stopped meaning what they meant, and a score for it would look
        exactly like a real one.
        """
        from training.registry import ModelRegistry

        if not rl_available():
            raise TrainingUnavailableError(
                "The reinforcement-learning stack is not installed. Install it with:\n"
                "    pip install -r requirements-ml.txt"
            )
        if episodes < 1:
            raise TrainingRefusedError("episodes must be at least 1")
        if episodes > self.settings.training.max_evaluation_episodes:
            raise TrainingRefusedError(
                f"{episodes} episodes is above the "
                f"{self.settings.training.max_evaluation_episodes} cap in configs/training.yaml"
            )
        if self.simulation_is_running():
            raise TrainingRefusedError(
                "a simulation is running — evaluating would compete with it for the same "
                "cores and both would slow down. Stop the simulation first."
            )

        # Raises if the model is missing or cannot mean anything here.
        entry = ModelRegistry(self.settings).require_runnable(model_id)

        with self._lock:
            if self._current is not None and self._current.state not in FINISHED:
                raise TrainingBusyError(
                    f"job {self._current.job_id} is already running — stop it before starting another"
                )
            job = TrainingJob(
                job_id=f"JOB-{int(time.time() * 1000) % 100_000_000:08d}-{next(_job_counter):03d}",
                kind="EVALUATE",
                model_id=model_id,
                algorithm=str(entry.get("algorithm") or "ppo"),
                requested_timesteps=0,
                seed=seed if seed is not None else self.settings.simulation.seed,
                evaluate_episodes=episodes,
            )
            self._current = job
            self._thread = threading.Thread(
                target=self._run_evaluation, args=(job, entry), name=f"evaluate-{job.job_id}", daemon=True
            )
            self._thread.start()

        log.info(
            "evaluation job started",
            extra={
                "event": "EVALUATION_JOB_STARTED",
                "job_id": job.job_id,
                "model": model_id,
                "episodes": episodes,
            },
        )
        return job

    def stop(self) -> TrainingJob | None:
        """Ask the running job to stop at the next step boundary."""
        with self._lock:
            job = self._current
            if job is None or job.state in FINISHED:
                return None
            job.cancel_requested = True
            if job.state == "RUNNING":
                job.state = "STOPPING"
        log.info("training job cancellation requested", extra={"event": "TRAINING_JOB_STOPPING"})
        return job

    def join(self, timeout: float | None = None) -> None:
        """Wait for the running job. Used by tests; the API never blocks on it."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    # ----------------------------------------------------------------- worker

    def _run_evaluation(self, job: TrainingJob, entry: dict[str, Any]) -> None:
        from training.registry import ModelRegistry

        pipeline = TrainingPipeline(self.settings, repository=self.repository)
        registry = ModelRegistry(self.settings)
        with self._lock:
            job.state = "RUNNING"

        started = time.perf_counter()

        def on_episode(done: int, total: int, mean_reward: float) -> bool:
            with self._lock:
                job.timesteps = done
                job.metrics.append(
                    MetricPoint(
                        timesteps=done,
                        elapsed_s=time.perf_counter() - started,
                        episode_reward_mean=round(mean_reward, 4),
                    )
                )
                return not job.cancel_requested

        try:
            evaluation = pipeline.evaluate_saved(
                entry["path"],
                episodes=job.evaluate_episodes,
                seed=job.seed,
                on_episode=on_episode,
            )
            # Only a complete evaluation goes on the card. A mean over two of
            # five episodes is a different measurement, and writing it as the
            # policy's score would misrepresent it every time it was read after.
            if not evaluation.get("cancelled"):
                registry.save_evaluation(job.model_id or "", evaluation)

            with self._lock:
                job.state = "CANCELLED" if evaluation.get("cancelled") else "COMPLETED"
                job.result = {"model_id": job.model_id, "evaluation": evaluation}
                job.ended_at = time.time()
            log.info(
                "evaluation job finished",
                extra={
                    "event": "EVALUATION_JOB_FINISHED",
                    "job_id": job.job_id,
                    "state": job.state,
                    "episodes": evaluation.get("episodes"),
                },
            )
        except BaseException as exc:  # the thread must report a failure, not vanish
            with self._lock:
                job.state = "FAILED"
                job.error = f"{type(exc).__name__}: {exc}"
                job.ended_at = time.time()
            log.exception(
                "evaluation job failed",
                extra={"event": "EVALUATION_JOB_FAILED", "job_id": job.job_id},
            )
        finally:
            with self._lock:
                self._history.append(job)
                self._history = self._history[-50:]

    def _run(self, job: TrainingJob) -> None:
        pipeline = TrainingPipeline(self.settings, repository=self.repository)
        with self._lock:
            job.state = "RUNNING"

        try:
            # Sample often enough for a readable curve, rarely enough that a
            # million-step job does not keep a million points in memory.
            every = max(1, job.requested_timesteps // 200)
            result = pipeline.train(
                job.algorithm,
                total_timesteps=job.requested_timesteps,
                seed=job.seed,
                evaluate_episodes=job.evaluate_episodes,
                callback=_progress_callback(job, self._lock, every),
            )
            cancelled = job.cancel_requested
            with self._lock:
                job.state = "CANCELLED" if cancelled else "COMPLETED"
                job.timesteps = result.total_timesteps
                job.result = result.to_dict()
                job.ended_at = time.time()
            if cancelled:
                # The model is real and saved; the history must not call a job
                # that was stopped early a completed one.
                pipeline.record_status(
                    result.training_id,
                    "CANCELLED",
                    f"stopped by the operator after {result.total_timesteps} steps",
                )
            log.info(
                "training job finished",
                extra={
                    "event": "TRAINING_JOB_FINISHED",
                    "job_id": job.job_id,
                    "state": job.state,
                    "timesteps": job.timesteps,
                },
            )
        except BaseException as exc:  # the thread must report a failure, not vanish
            with self._lock:
                job.state = "FAILED"
                job.error = f"{type(exc).__name__}: {exc}"
                job.ended_at = time.time()
            log.exception(
                "training job failed",
                extra={"event": "TRAINING_JOB_FAILED", "job_id": job.job_id},
            )
        finally:
            with self._lock:
                self._history.append(job)
                # Keep the list bounded; the database holds the real history.
                self._history = self._history[-50:]
