"""Where does a training step's time actually go?

Named `timing` and not `profile`, which is what it was called for about ten
minutes. `train.py` runs as a script, so its own directory is `sys.path[0]`,
and the standard library's `profile` module is what `cProfile` imports on its
first line. torch imports cProfile. A file called `competition/profile.py`
therefore stops training from starting at all, with an AttributeError six
frames inside torch._dynamo that names neither this file nor the collision.

Written before spending money on a faster machine, because the machine to buy
depends on the answer and the answer was not known. Measured on the laptop
mid-run: the GPU sat at 33% utilisation holding 429 MB of 6 GB, so whatever
the limit is, it is not GPU compute and it is not video memory.

Two timings and a subtraction:

* **environment** — the same vectorised environment the session trains in,
  stepped with random actions and no model at all. JSBSim, the opponent's
  forward pass, the scoring, the stick shaping.
* **everything** — the same number of steps through `model.learn`.

The difference is what learning costs: sampling the replay buffer, the
gradient steps, moving batches to the device. Subtracting rather than
instrumenting Stable-Baselines3 keeps this honest about what it measures —
it is wall-clock, which is what an overnight run is spent in.

The verdict says which machine would help, and names the case where none
would: many small updates on a small network is latency-bound, and a bigger
GPU does not make a kernel launch faster.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import numpy as np

SETTLE_STEPS = 200


def _config_from_card(card: dict[str, Any]) -> Any:
    """The session's own plant, the way the evaluator rebuilds one."""
    from competition.environment import EnvConfig
    from competition.safety import GroundAvoidance

    described = card.get("environment", {})
    floor = described.get("ground_avoidance")
    return EnvConfig(
        observation=described.get("observation", "reference"),
        action_repeat=int(described.get("action_repeat", 1)),
        rudder_enabled=bool(described.get("rudder_enabled", False)),
        rudder_limit=float(described.get("rudder_limit", 0.2)),
        high_speed_elevator_limit=float(described.get("high_speed_elevator_limit", 0.4)),
        speed_before_altitude=bool(described.get("speed_before_altitude", False)),
        ground_avoidance=GroundAvoidance(**floor) if floor is not None else None,
        g_limit=described.get("g_limit"),
        opponent=described.get("opponent", "reference"),
        opponent_aggression=float(described.get("opponent_aggression", 1.0)),
    )


def time_environment(config: Any, workers: int, steps: int, reward: str) -> float:
    """Seconds to take `steps` vectorised steps with no learning at all."""
    from competition.gym_env import make_vec_env

    env = make_vec_env(workers=workers, config=config, reward_mode=reward, seed=0, monitor=False)
    try:
        env.reset()
        actions = np.zeros((workers, env.action_space.shape[0]), dtype=np.float32)
        rng = np.random.default_rng(0)
        for _ in range(SETTLE_STEPS // workers + 1):
            env.step(actions)

        started = time.perf_counter()
        for _ in range(max(1, steps // workers)):
            actions = np.asarray(rng.uniform(-1.0, 1.0, actions.shape), dtype=np.float32)
            env.step(actions)
        return time.perf_counter() - started
    finally:
        env.close()


def time_everything(config: Any, card: dict[str, Any], workers: int, steps: int, reward: str) -> float:
    """Seconds for the same steps through `learn`, model and all."""
    from competition.gym_env import make_vec_env
    from competition.runtime import load_algorithm

    hyper = card.get("hyperparameters", {})
    env = make_vec_env(workers=workers, config=config, reward_mode=reward, seed=0, monitor=False)
    try:
        model = load_algorithm(card.get("algorithm", "sac"))(
            "MlpPolicy",
            env,
            verbose=0,
            gamma=float(hyper.get("gamma", 0.99)),
            learning_rate=float(hyper.get("learning_rate", 3e-4)),
            batch_size=int(hyper.get("batch_size", 256)),
            gradient_steps=int(hyper.get("gradient_steps", 1)),
            policy_kwargs={"net_arch": list(hyper.get("hidden", [256, 256]))},
            learning_starts=0,
        )
        model.learn(total_timesteps=SETTLE_STEPS, log_interval=10**9)
        started = time.perf_counter()
        model.learn(total_timesteps=steps, reset_num_timesteps=False, log_interval=10**9)
        return time.perf_counter() - started
    finally:
        env.close()


def verdict(env_s: float, total_s: float, workers: int) -> list[str]:
    """What to do about the split, including "nothing, and keep your money"."""
    learning_s = max(0.0, total_s - env_s)
    share = learning_s / total_s if total_s else 0.0

    if share > 0.65:
        return [
            f"Learning is {share:.0%} of the time, the simulation {1 - share:.0%}.",
            "",
            "  A faster machine is the wrong purchase first. With gradient_steps",
            "  -1 and a small network this is many tiny GPU calls, and a bigger",
            "  GPU does not make a kernel launch faster. Try, in this order:",
            "    --gradient-steps 2      fewer, larger updates per batch of steps",
            "    --batch-size 512        more work per call to the same GPU",
            "  Both change the experiment, so measure the result, do not assume it.",
        ]
    if share < 0.35:
        return [
            f"The simulation is {1 - share:.0%} of the time, learning {share:.0%}.",
            "",
            f"  This is CPU work, and it parallelises. You are using {workers}",
            "  workers; if the machine has more cores than that, --workers is the",
            "  cheapest speed available and it costs nothing.",
            "  If you do rent a machine, buy cores, not a bigger GPU.",
        ]
    return [
        f"Learning {share:.0%}, simulation {1 - share:.0%} — no single bottleneck.",
        "",
        "  Nothing here is worth buying hardware for. More workers help the",
        "  simulation half only, up to the number of cores.",
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", help="a trained session, e.g. models/competition/v4")
    parser.add_argument("--steps", type=int, default=2000, help="steps to time in each phase")
    parser.add_argument("--workers", type=int, default=None, help="default: what the card used")
    args = parser.parse_args(argv)

    session = Path(args.session)
    card_path = session / "card.json"
    if not card_path.is_file():
        print(f"no card.json in {session} — a session has to have finished at least once")
        return 1

    card = json.loads(card_path.read_text(encoding="utf-8"))
    config = _config_from_card(card)
    workers = args.workers or int(card.get("workers", 8))
    reward = card.get("reward", "reference")

    print()
    print(f"Timing {args.steps:,} steps of {session.name}, {workers} workers")
    print("  (the same plant and hyperparameters the card records)")
    print()

    print("  1/2  simulation only, no model      ... ", end="", flush=True)
    env_s = time_environment(config, workers, args.steps, reward)
    print(f"{env_s:6.1f} s   {args.steps / env_s:6.0f} steps/s")

    print("  2/2  the same steps through learn   ... ", end="", flush=True)
    total_s = time_everything(config, card, workers, args.steps, reward)
    print(f"{total_s:6.1f} s   {args.steps / total_s:6.0f} steps/s")

    print()
    for line in verdict(env_s, total_s, workers):
        print(line)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
