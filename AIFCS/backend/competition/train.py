"""Training entry point for the competition policy.

    python backend/competition/train.py --algorithm ppo --timesteps 200000

Run as a module with a `__main__` guard, which is not a style preference:
`SubprocVecEnv` starts each worker as a fresh interpreter on Windows, and each
worker re-imports the launching module. Without the guard the script re-runs
itself, spawns more workers, and the machine stops.

Every run records what it was trained against — reward, opponent, initial
conditions, which F-16, whether the rudder was unlocked — because none of those
are obvious from a `.zip` and a policy is only meaningful against the
environment that shaped it.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

if __package__ in (None, ""):  # running the file directly
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from competition.environment import EnvConfig, RoundSetup
from competition.gym_env import make_vec_env
from competition.rewards import RewardMode

ALGORITHMS = ("ppo", "sac")


def build_config(args: argparse.Namespace) -> EnvConfig:
    setup = RoundSetup.reference() if args.reference_setup else RoundSetup()
    return EnvConfig(
        setup=setup,
        jsbsim_root=args.jsbsim_root,
        rudder_enabled=args.rudder,
        opponent=args.opponent,
        speed_before_altitude=args.reference_speed_order,
    )


def train(args: argparse.Namespace) -> Path:
    import torch as th
    from stable_baselines3 import PPO, SAC

    config = build_config(args)
    env = make_vec_env(args.workers, config, args.reward, seed=args.seed)

    policy_kwargs = {"net_arch": [256, 256], "activation_fn": th.nn.Tanh}
    common = {
        "policy": "MlpPolicy",
        "env": env,
        "seed": args.seed,
        "verbose": 1,
        "device": args.device,
        "policy_kwargs": policy_kwargs,
    }
    model: PPO | SAC
    if args.algorithm == "ppo":
        model = PPO(n_steps=args.n_steps, batch_size=args.batch_size, **common)
    else:
        # SAC has no n_steps: it learns from a replay buffer, not from rollouts.
        model = SAC(batch_size=args.batch_size, **common)

    started = time.perf_counter()
    model.learn(total_timesteps=args.timesteps, progress_bar=False)
    elapsed = time.perf_counter() - started

    directory = Path(args.output)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    name = f"comp-{args.algorithm}-{stamp}"
    model_path = directory / f"{name}.zip"
    model.save(model_path)

    card = {
        "training_id": name,
        "algorithm": args.algorithm,
        "requested_timesteps": args.timesteps,
        "trained_timesteps": int(getattr(model, "num_timesteps", args.timesteps)),
        "elapsed_s": round(elapsed, 1),
        "steps_per_second": round(args.timesteps / elapsed, 1) if elapsed else None,
        "seed": args.seed,
        "workers": args.workers,
        "device": args.device,
        "reward": str(args.reward),
        "environment": config.describe(),
    }
    (directory / f"{name}.json").write_text(json.dumps(card, indent=2), encoding="utf-8")
    env.close()

    print(f"\ntrained {card['trained_timesteps']} steps in {elapsed:.1f}s")
    print(f"model: {model_path}")
    print(f"card:  {directory / (name + '.json')}")
    return model_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--algorithm", choices=ALGORITHMS, default="ppo")
    parser.add_argument("--timesteps", type=int, default=200_000)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--n-steps", type=int, default=2048)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--output", default="models/competition")
    parser.add_argument(
        "--reward",
        choices=[mode.value for mode in RewardMode],
        default=RewardMode.REFERENCE.value,
        help="reference reproduces the package's shaping; score optimises what the judges add up",
    )
    parser.add_argument(
        "--opponent",
        choices=["reference", "level"],
        default="reference",
        help="reference is the ported auto_run; level flies a fixed throttle and runs away",
    )
    parser.add_argument("--rudder", action="store_true", help="unlock the rudder channel")
    parser.add_argument(
        "--jsbsim-root",
        default=None,
        help="data root holding aircraft/f16; unset uses the installed JSBSim",
    )
    parser.add_argument(
        "--reference-setup",
        action="store_true",
        help="train on the package's initial conditions instead of the published ones",
    )
    parser.add_argument(
        "--reference-speed-order",
        action="store_true",
        help="reproduce the package's initial-condition ordering, 106 knots slow",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    train(parse_args())
