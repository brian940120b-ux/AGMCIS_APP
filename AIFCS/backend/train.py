#!/usr/bin/env python
"""Train an AIFCS flight policy from the command line (PHASE 13).

    cd backend
    ../.venv/bin/python train.py --algorithm ppo --timesteps 200000 --evaluate 5

Training is CLI-only for now. Driving a long job from the browser needs job
management — progress, cancellation, surviving a reload — which is the training
centre's work in a later phase. Rather than put a button in the dashboard that
cannot do those things, the dashboard says training is run from here.

Everything is fictional and abstract. The task is flight and navigation; this
platform models no weapon, engagement or targeting capability.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running this file directly from backend/ without installing anything.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.config import get_settings
from core.logging_config import configure_logging
from training.pipeline import (
    ALGORITHMS,
    TrainingPipeline,
    TrainingUnavailableError,
    resolve_device,
    rl_available,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="train.py",
        description="Train a flight policy against the AIFCS simulation.",
    )
    parser.add_argument("--algorithm", choices=ALGORITHMS, default="ppo")
    parser.add_argument(
        "--timesteps",
        type=int,
        default=None,
        help="Override total_timesteps from configs/training.yaml.",
    )
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument(
        "--episode-seconds",
        type=float,
        default=None,
        help="Override the episode length. Shorter episodes learn faster early on.",
    )
    parser.add_argument(
        "--evaluate",
        type=int,
        default=5,
        metavar="EPISODES",
        help="Episodes to evaluate after training. 0 skips evaluation.",
    )
    parser.add_argument("--progress", action="store_true", help="Show a progress bar.")
    parser.add_argument("--list", action="store_true", help="List saved models and exit.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    configure_logging(
        level=settings.logging.level,
        json_format=settings.logging.json_format,
        directory=settings.logging.directory,
        project_root=settings.project_root,
    )

    if not rl_available():
        print(
            "The reinforcement-learning stack is not installed.\n"
            "    .venv/bin/pip install -r requirements-ml.txt",
            file=sys.stderr,
        )
        return 2

    pipeline = TrainingPipeline(settings=settings)

    if args.list:
        models = pipeline.list_models()
        if not models:
            print("No saved models yet.")
            return 0
        for entry in models:
            card = entry.get("card") or {}
            evaluation = card.get("evaluation") or {}
            print(
                f"{entry['model_id']:34s} {entry['algorithm']:4s} "
                f"{card.get('total_timesteps', '?'):>9} steps  "
                f"reward {evaluation.get('mean_reward', '—')}"
            )
        return 0

    if args.episode_seconds is not None:
        settings = settings.model_copy(
            update={
                "training": settings.training.model_copy(update={"max_episode_seconds": args.episode_seconds})
            }
        )
        pipeline = TrainingPipeline(settings=settings)

    print(
        f"Training {args.algorithm.upper()} on {settings.training.scenario} "
        f"({resolve_device(settings.training.device)}), "
        f"{args.timesteps or pipeline.algorithm_settings(args.algorithm).total_timesteps} timesteps."
    )
    print("This runs the real simulation, so it is not fast. Ctrl+C stops it.\n")

    try:
        result = pipeline.train(
            args.algorithm,
            total_timesteps=args.timesteps,
            seed=args.seed,
            evaluate_episodes=args.evaluate,
            progress=args.progress,
        )
    except TrainingUnavailableError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nStopped. No model was saved.", file=sys.stderr)
        return 130

    print(f"\nSaved {result.model_path}")
    print(f"Card  {result.card_path}")
    if result.evaluation:
        print("\nEvaluation:")
        print(json.dumps(result.evaluation, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
