"""Training that survives the machine being turned off.

A process cannot outlive a shutdown. What can outlive it is everything the
process knew, written to disk often enough that losing the process costs
minutes rather than days — so "keep training overnight, shut the laptop down,
carry on tomorrow" becomes checkpoint, resume, and never repeat work.

A session is a directory:

    models/competition/<name>/
        checkpoint.zip          the policy and its optimiser state
        replay_buffer.pkl       SAC's experience, when kept
        state.json             steps done, and what they were done against
        card.json              the finished model's card

`state.json` is written **after** the model, and read before anything else. A
crash between the two loses one checkpoint interval and leaves a consistent
pair; the other order would leave a state file claiming steps that the model
does not have.

Resuming is the default. Asking for 5,000,000 steps on a session that has done
3,000,000 trains 2,000,000 more; asking again does nothing and says so. The
target is a total, not an increment, because "train until it has had five
million steps" is the sentence someone actually means.
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import platform
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.logging_config import get_logger

log = get_logger("competition.session")

STATE_VERSION = 1


@dataclass
class SessionState:
    """What a resumed run has to know that the `.zip` does not carry."""

    name: str
    algorithm: str
    timesteps_done: int = 0
    target_timesteps: int = 0
    wall_clock_s: float = 0.0
    runs: int = 0
    reward: str = "reference"
    environment: dict[str, Any] = field(default_factory=dict)
    seed: int = 0
    workers: int = 1
    created_at: str = ""
    updated_at: str = ""
    version: int = STATE_VERSION

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class IncompatibleSession(RuntimeError):
    """The session on disk was trained against something else.

    Resuming across a changed environment or reward produces a policy whose
    steps were spent on two different problems, and whose card can only be
    wrong about one of them. Refusing is the honest option; the message says
    what differs so the choice is to rename or to start again.
    """


@dataclass
class Session:
    """A directory that a training run can be resumed from."""

    root: Path

    @property
    def model_path(self) -> Path:
        return self.root / "checkpoint.zip"

    @property
    def buffer_path(self) -> Path:
        return self.root / "replay_buffer.pkl"

    @property
    def state_path(self) -> Path:
        return self.root / "state.json"

    @property
    def card_path(self) -> Path:
        return self.root / "card.json"

    @property
    def exists(self) -> bool:
        return self.state_path.is_file() and self.model_path.is_file()

    def read_state(self) -> SessionState | None:
        if not self.state_path.is_file():
            return None
        raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        known = set(SessionState.__dataclass_fields__)
        return SessionState(**{k: v for k, v in raw.items() if k in known})

    def write_state(self, state: SessionState) -> None:
        """Replace the state file atomically.

        A half-written state file read on the next start is worse than no state
        file at all: it would either fail to parse or, worse, parse into a step
        count that is not the model's.
        """
        state.updated_at = datetime.now(UTC).isoformat()
        temporary = self.state_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(state.as_dict(), indent=2), encoding="utf-8")
        temporary.replace(self.state_path)

    def check_compatible(self, state: SessionState, wanted: SessionState) -> None:
        """Refuse to continue a run against a different problem."""
        differences = []
        if state.algorithm != wanted.algorithm:
            differences.append(f"algorithm {state.algorithm} -> {wanted.algorithm}")
        if state.reward != wanted.reward:
            differences.append(f"reward {state.reward} -> {wanted.reward}")
        for key, old in state.environment.items():
            new = wanted.environment.get(key)
            if new != old:
                differences.append(f"{key} {old!r} -> {new!r}")
        if differences:
            raise IncompatibleSession(
                f"session {state.name!r} has {state.timesteps_done:,} steps trained against "
                "something else:\n  " + "\n  ".join(differences) + "\n"
                "Use a different --name, or delete the session directory to start again."
            )


class KeepAwake:
    """Stop Windows sleeping in the middle of an overnight run.

    A laptop that suspends loses nothing — the checkpoint is on disk — but it
    also stops training, and someone who set a run going before bed would find
    it three minutes old in the morning. Windows is asked to keep the system
    running while allowing the display to sleep, which is the combination that
    matters.

    A no-op everywhere else, and never an error: not being able to prevent
    sleep is not a reason to refuse to train.
    """

    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001

    def __init__(self) -> None:
        self.held = False

    def __enter__(self) -> KeepAwake:
        if platform.system() != "Windows":
            return self
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(  # type: ignore[attr-defined]
                self.ES_CONTINUOUS | self.ES_SYSTEM_REQUIRED
            )
            self.held = True
            log.info(
                "asked Windows not to sleep while training",
                extra={"event": "COMPETITION_KEEP_AWAKE"},
            )
        except Exception as exc:  # never a reason not to train
            log.warning(
                "could not ask Windows to stay awake",
                extra={"event": "COMPETITION_KEEP_AWAKE_FAILED", "detail": str(exc)},
            )
        return self

    def __exit__(self, *_: object) -> None:
        if not self.held:
            return
        with contextlib.suppress(Exception):  # the process is ending anyway
            ctypes.windll.kernel32.SetThreadExecutionState(  # type: ignore[attr-defined]
                self.ES_CONTINUOUS
            )


def tensorboard_log_dir(root: Path) -> str | None:
    """A log directory, or None when TensorBoard is not installed.

    Stable-Baselines3 raises ImportError out of `learn()` when handed a
    `tensorboard_log` it cannot use — not at construction, and not with a
    warning. An overnight run should not be lost because an optional way of
    drawing graphs is absent, and `requirements-ml.txt` listing tensorboard
    does not make every machine have it.
    """
    try:
        import tensorboard  # noqa: F401
    except ImportError:
        log.info(
            "tensorboard is not installed — training without its logs",
            extra={"event": "COMPETITION_NO_TENSORBOARD"},
        )
        return None
    return str(root / "logs")


def checkpoint_callback(
    session: Session,
    state: SessionState,
    every: int,
    save_buffer: bool,
):
    """An SB3 callback that writes a resumable checkpoint every `every` steps.

    Built here rather than imported at module scope because `BaseCallback` only
    exists when the RL stack is installed, and this module must import without
    it — the same rule the rest of the platform follows.
    """
    from stable_baselines3.common.callbacks import BaseCallback

    class _Checkpoint(BaseCallback):  # type: ignore[misc]
        def __init__(self) -> None:
            super().__init__(verbose=0)
            self.started_steps = state.timesteps_done
            self.started_at = time.perf_counter()
            self.next_at = every

        def _on_step(self) -> bool:
            if self.num_timesteps >= self.next_at:
                self.next_at = self.num_timesteps + every
                self.save()
            return True

        def save(self) -> None:
            save_checkpoint(
                self.model,
                session,
                state,
                steps_this_run=int(self.num_timesteps),
                started_steps=self.started_steps,
                elapsed_s=time.perf_counter() - self.started_at,
                save_buffer=save_buffer,
            )

    return _Checkpoint()


def save_checkpoint(
    model: Any,
    session: Session,
    state: SessionState,
    *,
    steps_this_run: int,
    started_steps: int,
    elapsed_s: float,
    save_buffer: bool,
) -> None:
    """Model first, then state. Never the other way round."""
    session.root.mkdir(parents=True, exist_ok=True)
    model.save(session.model_path)
    if save_buffer and hasattr(model, "save_replay_buffer"):
        model.save_replay_buffer(session.buffer_path)

    state.timesteps_done = started_steps + steps_this_run
    state.wall_clock_s = round(state.wall_clock_s + elapsed_s, 1)
    session.write_state(state)

    log.info(
        "checkpoint written",
        extra={
            "event": "COMPETITION_CHECKPOINT",
            "session": state.name,
            "timesteps_done": state.timesteps_done,
            "target": state.target_timesteps,
        },
    )


def human_duration(seconds: float) -> str:
    """For a progress line someone reads at two in the morning."""
    seconds = max(0.0, seconds)
    if seconds < 90:
        return f"{seconds:.0f}s"
    minutes = seconds / 60
    if minutes < 90:
        return f"{minutes:.0f} min"
    hours = minutes / 60
    if hours < 48:
        return f"{hours:.1f} hr"
    return f"{hours / 24:.1f} days"


def describe_progress(state: SessionState, steps_per_second: float | None) -> str:
    done, target = state.timesteps_done, state.target_timesteps
    fraction = done / target if target else 0.0
    line = f"{done:,} / {target:,} steps ({fraction * 100:.1f}%)"
    if steps_per_second and done < target:
        line += f" — about {human_duration((target - done) / steps_per_second)} left"
    return line
