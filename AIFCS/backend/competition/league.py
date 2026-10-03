"""Opponents that are themselves policies, and a pool to draw them from.

The opponent on the day is another team's agent. Everything we have trained
against so far is a script — the reference package's target drone, which flies
straight, and a pursuit controller. A policy that has only ever met scripts
learns to beat scripts, and the measurement already shows how far that goes: a
policy doing *nothing at all*, with a ground-avoidance floor under it, wins two
rounds in three against the drone.

PHANG-MAN (Pope, Ide et al., AlphaDogfight Trials, arXiv 2105.00990) laid out
the ladder this implements:

    Agents initially trained only against opponents with scripted behaviors
    that were considered "easy". Once the agent achieved a win/loss ratio
    greater than 50%, additional "intelligent" opponents were introduced...
    After the agent played every opponent 100 times and had an overall
    win/loss ratio greater than 50%, opponents were sampled from a probability
    distribution proportional to the win/loss ratio of the last 100 matchups
    against every opponent... The probability of playing any opponent was
    clipped to a minimum of 0.2% and a maximum of 11.7%.

Their thresholds are used here rather than invented ones. Thirty opponents plus
self-play is what they ran; we will have fewer, and the arithmetic does not
care how many.

What this is not: a way to run two learners against each other. A pool
opponent's weights are frozen when it enters the pool, which is what keeps the
environment stationary enough for an off-policy learner to use a replay buffer
at all.
"""

from __future__ import annotations

import json
import random
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from competition.action import (
    ELEVATOR_LIMIT_HIGH_SPEED,
    RUDDER_LIMIT,
    JoystickState,
    shape_command,
)
from competition.state import StateEncoder, Telemetry

#: PHANG-MAN's clips, so no opponent is ever unplayable or the only one played.
MIN_SHARE = 0.002
MAX_SHARE = 0.117
#: Their gate: sampling stays uniform until the agent is winning overall.
GATE_WIN_RATE = 0.5
#: And their window: the last hundred matchups against each opponent.
WINDOW = 100

#: The other scheme, from the winners of the same problem in Korea
#: (research/sources.yaml SRC-012, train.py defaults): no gate and no window.
#: Each opponent keeps an exponential moving average of our results against
#: it, and sampling is half uniform, half a softmax that favours whoever the
#: average says is beating us. It starts weighting from the first round, which
#: matters here: the paper's gate needs every opponent met a hundred times per
#: worker, and at 2,000,000 steps over eight workers a worker sees a few dozen
#: rounds in all — the paper's weighting never switches on in our budget.
LEAGUE_SCHEMES = ("paper", "ema")
EMA_ALPHA = 0.1
EMA_TEMPERATURE = 0.3
EMA_UNIFORM_FLOOR = 0.5


class PolicyOpponent:
    """A saved policy flying the opposing aircraft.

    The opponent side of a round is handed raw control surfaces rather than a
    policy's action, so the stick shaping is applied here — with the same
    function the player's side uses, because an opponent flying an unshaped
    aircraft is not flying the same aircraft.

    A class rather than a closure so that `reset` is a method and the held
    decision has somewhere to live that a type checker can see.
    """

    def __init__(
        self,
        predict: Callable[[np.ndarray], np.ndarray],
        *,
        action_repeat: int = 1,
        rudder_limit: float = RUDDER_LIMIT,
        high_speed_elevator_limit: float = ELEVATOR_LIMIT_HIGH_SPEED,
        encoder: Any = None,
        ground_avoidance: Any = None,
        g_limit: float | None = None,
    ) -> None:
        if action_repeat < 1:
            raise ValueError(f"action_repeat is a number of frames, not {action_repeat}")
        self.predict = predict
        self.action_repeat = action_repeat
        self.rudder_limit = rudder_limit
        self.high_speed_elevator_limit = high_speed_elevator_limit
        # The encoder the saved policy was trained with, not the reference one.
        # Hardcoding StateEncoder() here meant a 30-dimensional policy was fed
        # 20 numbers: v4 trains on `extended`, so the first attempt to put it
        # in a pool would have failed on a shape mismatch before the first
        # round finished.
        self.encoder = encoder if encoder is not None else StateEncoder()
        #: The rule layer this policy learned to fly with, if it had one. An
        #: opponent without it is not the opponent that was trained: v4 learned
        #: under a floor, and flown as a pool opponent without one it flies
        #: into the ground. v5 then spent 2,000,000 steps learning to beat
        #: something that kills itself, and came out weaker than v4.
        self.ground_avoidance = ground_avoidance
        #: v4 learned under a load-factor limit. Without it the opponent
        #: flies the sample's Mach-based one instead, which is half the
        #: turn rate above Mach 0.8 — a different aeroplane again.
        self.g_limit = g_limit
        self.joystick = JoystickState()
        self._held: np.ndarray | None = None
        self._frames = 0

    def reset(self) -> None:
        self.encoder.reset()
        self.joystick.reset()
        self._held = None
        self._frames = 0

    def __call__(self, telemetry: Telemetry) -> np.ndarray:
        # Encoded every frame, decided every `action_repeat` frames, exactly
        # as the training wrapper and the day's client do it. The extended
        # encoder's rate fields are the difference from the *previous call*
        # times 60 Hz, and its clock counts calls: encoding only on decision
        # frames handed this policy rates six times too large and a round
        # that aged at a sixth of the speed, which is not the policy that was
        # trained. The seat under test, encoded every frame, won 85% against
        # the same checkpoint sitting here.
        observation = self.encoder.encode(telemetry)
        if self._held is None or self._frames >= self.action_repeat:
            self._held = np.asarray(self.predict(observation), dtype=np.float64)
            self._frames = 0
        self._frames += 1
        action = self._held
        if self.ground_avoidance is not None:
            # Before the shaping, exactly where the player's side applies it,
            # so a recovery is rate-limited the same way.
            action = self.ground_avoidance(action, telemetry)
        return shape_command(
            action,
            self.joystick,
            telemetry.reference_mach,
            high_speed_elevator_limit=self.high_speed_elevator_limit,
            rudder_limit=self.rudder_limit,
            g_load=None if self.g_limit is None else telemetry.own_g_acc,
            g_limit=self.g_limit,
        )


def collect_checkpoints(paths: list[str]) -> dict[str, Path]:
    """Every saved policy under the given files or directories, by name.

    A directory is expanded to the `.zip` files in it, so a pool can be grown
    by dropping a checkpoint in rather than by editing a command line — which
    is what self-play looks like in practice: snapshot the session now and
    again, and the snapshots become the ladder.
    """
    found: dict[str, Path] = {}
    for entry in paths:
        path = Path(entry)
        if path.is_dir():
            candidates = sorted(path.glob("*.zip"))
            if not candidates:
                raise FileNotFoundError(f"no .zip policies in {path}")
        elif path.is_file():
            candidates = [path]
        else:
            raise FileNotFoundError(f"no policy at {path}")
        for candidate in candidates:
            # Named by the session it came from where that is unambiguous, so
            # a league's statistics read as something a person recognises.
            name = candidate.stem
            if name in {"checkpoint", "model"}:
                name = candidate.parent.name
            if name in found:
                name = f"{name}-{len(found)}"
            found[name] = candidate
    return found


def extract_actor(model: Any) -> dict[str, Any]:
    """A trained SAC actor as plain numpy arrays.

    The point is what the workers then do *not* need. A pool opponent used to
    be a loaded SB3 model, which meant every `SubprocVecEnv` worker imported
    torch, and every torch import brings an OpenMP thread pool sized to the
    machine. Eight of those is how v5's second attempt died:

        OMP: Error #137: Cannot create thread.
        OMP: System error #1450

    A SAC actor is a small MLP — two hidden layers and a linear head — so the
    forward pass is three matrix multiplies and a tanh. In numpy the workers
    stay what they were before self-play existed: JSBSim and arrays.

    Only SAC. PPO's actor is shaped differently and is not squashed the same
    way, and guessing at it would be worse than saying so.
    """
    import torch

    actor = model.policy.actor
    layers: list[dict[str, Any]] = []
    for module in [*actor.latent_pi, actor.mu]:
        if isinstance(module, torch.nn.Linear):
            layers.append(
                {
                    "kind": "linear",
                    "weight": module.weight.detach().cpu().numpy().astype(np.float64),
                    "bias": module.bias.detach().cpu().numpy().astype(np.float64),
                }
            )
        elif isinstance(module, torch.nn.ReLU):
            layers.append({"kind": "relu"})
        elif isinstance(module, torch.nn.Tanh):
            layers.append({"kind": "tanh"})
        else:
            raise NotImplementedError(f"the actor has a {type(module).__name__} this cannot replay in numpy")

    low = np.asarray(model.action_space.low, dtype=np.float64)
    high = np.asarray(model.action_space.high, dtype=np.float64)
    return {"layers": layers, "low": low, "high": high, "squash": bool(model.policy.squash_output)}


class NumpyActor:
    """The forward pass of `extract_actor`'s weights, with no torch anywhere.

    Deterministic only, which is what an opponent wants: a pool that samples
    its actions is a pool whose difficulty moves under the policy learning
    against it.
    """

    def __init__(self, weights: dict[str, Any]) -> None:
        self.weights = weights

    def __call__(self, state: np.ndarray) -> np.ndarray:
        value = np.asarray(state, dtype=np.float64)
        for layer in self.weights["layers"]:
            if layer["kind"] == "linear":
                value = layer["weight"] @ value + layer["bias"]
            elif layer["kind"] == "relu":
                value = np.maximum(value, 0.0)
            else:
                value = np.tanh(value)
        if self.weights["squash"]:
            # tanh, then off [-1, 1] and onto the action space, which is what
            # SB3 does in unscale_action.
            value = np.tanh(value)
            low, high = self.weights["low"], self.weights["high"]
            value = low + 0.5 * (value + 1.0) * (high - low)
        return np.clip(value, self.weights["low"], self.weights["high"])


def _floor_from(described: dict[str, Any]) -> Any:
    """The ground-avoidance layer a session recorded, rebuilt."""
    from competition.safety import GroundAvoidance

    floor = described.get("ground_avoidance")
    return GroundAvoidance(**floor) if floor is not None else None


def opponent_from_checkpoint(
    checkpoint: Path,
    algorithm: str = "sac",
    device: str = "cpu",
) -> PolicyOpponent:
    """A pool opponent flying the aircraft its own session trained on.

    Every setting comes from the checkpoint's sibling `card.json`, the way the
    evaluator rebuilds a session's plant: observation, decision rate, rudder
    cap, high-speed elevator limit. Taking them from the *new* run's flags
    instead — which is what this used to do — makes the opponent a policy
    flying somebody else's aeroplane, and a 30-dimensional policy handed 20
    numbers does not fly at all.

    A card that is missing, or missing a field, falls back to the reference
    value, which is what a session recorded before those fields existed had.
    """
    from competition.environment import EnvConfig, build_encoder

    card_path = checkpoint.parent / "card.json"
    described: dict[str, Any] = {}
    if card_path.is_file():
        described = json.loads(card_path.read_text(encoding="utf-8")).get("environment", {})

    return PolicyOpponent(
        load_actor(checkpoint, algorithm, device=device),
        action_repeat=int(described.get("action_repeat", 1)),
        rudder_limit=float(described.get("rudder_limit", RUDDER_LIMIT)),
        high_speed_elevator_limit=float(
            described.get("high_speed_elevator_limit", ELEVATOR_LIMIT_HIGH_SPEED)
        ),
        encoder=build_encoder(EnvConfig(observation=described.get("observation", "reference"))),
        ground_avoidance=_floor_from(described),
        g_limit=described.get("g_limit"),
    )


def load_actor(checkpoint: Path, algorithm: str = "sac", device: str = "cpu") -> NumpyActor:
    """A saved policy as numpy weights, loaded here and never sent as a model.

    The parent process has torch loaded already — it is the one training. The
    workers do not, and this is what keeps it that way: what crosses the pipe
    to each of them is three small arrays, not a neural network and the
    OpenMP thread pool that comes with importing one.
    """
    from competition.runtime import load_algorithm

    model = load_algorithm(algorithm).load(str(checkpoint), device=device)
    return NumpyActor(extract_actor(model))


def load_predict(checkpoint: Path, algorithm: str = "sac", device: str = "cpu"):
    """A saved policy as a plain function, with no Gymnasium in the way."""
    from competition.runtime import load_algorithm

    model = load_algorithm(algorithm).load(str(checkpoint), device=device)

    def predict(state: np.ndarray) -> np.ndarray:
        action, _ = model.predict(state, deterministic=True)
        return np.asarray(action, dtype=np.float64)

    return predict


@dataclass
class Record:
    """How the last `WINDOW` matchups against one opponent went."""

    name: str
    results: deque[bool] = field(default_factory=lambda: deque(maxlen=WINDOW))
    #: Exponential moving average of our win rate against this opponent, for
    #: the "ema" scheme. Starts at a half, like `win_rate` on no evidence.
    ema: float = 0.5

    @property
    def played(self) -> int:
        return len(self.results)

    @property
    def wins(self) -> int:
        return sum(self.results)

    @property
    def win_rate(self) -> float:
        """Half on no evidence, so an unplayed opponent is neither feared nor
        dismissed before it has been met."""
        return self.wins / self.played if self.played else 0.5


@dataclass
class League:
    """Which opponent to play next, by the paper's rule.

    Uniform until the agent is winning overall and has met everyone the
    prescribed number of times; then weighted towards whoever is beating it.
    """

    names: list[str]
    gate_win_rate: float = GATE_WIN_RATE
    window: int = WINDOW
    min_share: float = MIN_SHARE
    max_share: float = MAX_SHARE
    seed: int = 0
    #: "paper" is PHANG-MAN's rule above; "ema" is SRC-012's, see LEAGUE_SCHEMES.
    scheme: str = "paper"
    ema_alpha: float = EMA_ALPHA
    temperature: float = EMA_TEMPERATURE
    uniform_floor: float = EMA_UNIFORM_FLOOR

    def __post_init__(self) -> None:
        if not self.names:
            raise ValueError("a league needs at least one opponent")
        if self.scheme not in LEAGUE_SCHEMES:
            raise ValueError(f"league scheme must be one of {LEAGUE_SCHEMES}, not {self.scheme!r}")
        if not 0.0 <= self.uniform_floor <= 1.0:
            raise ValueError(f"uniform_floor is a share, not {self.uniform_floor}")
        self.records = {name: Record(name) for name in self.names}
        self.random = random.Random(self.seed)

    # ------------------------------------------------------------- outcomes

    def record(self, name: str, won: bool) -> None:
        record = self.records[name]
        record.results.append(won)
        record.ema = (1.0 - self.ema_alpha) * record.ema + self.ema_alpha * float(won)

    @property
    def overall_win_rate(self) -> float:
        played = sum(r.played for r in self.records.values())
        if not played:
            return 0.0
        return sum(r.wins for r in self.records.values()) / played

    @property
    def weighting(self) -> bool:
        """Whether sampling has earned the right to stop being uniform.

        Both of the paper's conditions: every opponent met `window` times, and
        winning overall. Weighting before either would chase noise.
        """
        if any(r.played < self.window for r in self.records.values()):
            return False
        return self.overall_win_rate > self.gate_win_rate

    # -------------------------------------------------------------- choosing

    def shares(self) -> dict[str, float]:
        """The sampling distribution, clipped and renormalised."""
        if self.scheme == "ema":
            return self._ema_shares()
        if not self.weighting:
            uniform = 1.0 / len(self.names)
            return dict.fromkeys(self.names, uniform)

        # Proportional to how often they beat us, so the hard ones come up.
        raw = {name: 1.0 - self.records[name].win_rate for name in self.names}
        total = sum(raw.values())
        if total <= 0.0:  # we beat everyone every time
            raw = dict.fromkeys(self.names, 1.0)
            total = float(len(self.names))

        clipped = {
            name: min(max(value / total, self.min_share), self.max_share) for name, value in raw.items()
        }
        # Clipping breaks the sum; renormalising restores it and can push a
        # value back outside the clip, which is what the paper's own bounds
        # imply for any league smaller than 1/max_share opponents. Said rather
        # than hidden: with fewer than nine opponents the maximum cannot bind.
        scale = sum(clipped.values())
        return {name: value / scale for name, value in clipped.items()}

    def _ema_shares(self) -> dict[str, float]:
        """p_i = f/m + (1 - f) * softmax(-ema_i / tau): SRC-012's sampler.

        The uniform half is a floor nobody drops below, so an opponent that is
        being beaten every time still comes up — the paper's MIN_SHARE by
        another route. The softmax half sends the rest to whoever the moving
        average says we lose to.
        """
        count = len(self.names)
        logits = np.array([-self.records[name].ema / self.temperature for name in self.names])
        logits -= logits.max()  # stable
        soft = np.exp(logits)
        soft /= soft.sum()
        floor = self.uniform_floor / count
        return {
            name: floor + (1.0 - self.uniform_floor) * float(weight)
            for name, weight in zip(self.names, soft, strict=True)
        }

    def next_opponent(self) -> str:
        shares = self.shares()
        return self.random.choices(list(shares), weights=list(shares.values()), k=1)[0]


__all__ = [
    "EMA_ALPHA",
    "EMA_TEMPERATURE",
    "EMA_UNIFORM_FLOOR",
    "GATE_WIN_RATE",
    "LEAGUE_SCHEMES",
    "MAX_SHARE",
    "MIN_SHARE",
    "WINDOW",
    "League",
    "PolicyOpponent",
    "Record",
    "load_predict",
]
