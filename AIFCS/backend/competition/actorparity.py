"""Does the numpy replay of a saved actor say what the SB3 model says?

    python backend/competition/actorparity.py models/competition/v9_doctrine

The seat under test is flown through Stable-Baselines3's `predict`; a pool
opponent is the same weights replayed in numpy (`league.NumpyActor`), so that
training workers never import torch. The two are the same function on paper,
and a unit test measured them 1.8e-07 apart on random inputs. This asks the
question on the real checkpoint and on the observations the policy actually
meets: a round is flown, every frame's observation is kept, each is pushed
through both paths, and the largest difference per channel is printed.

Why it matters: with the simulator proven exactly symmetric under a seat
swap (the same hand policy on both chairs gives margins that negate to the
unit), any remaining difference between the chairs can only come from the
two inference paths. A difference of 1e-7 only decorrelates chaotic rounds;
a difference of 1e-2 is a different policy.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

import numpy as np

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from competition.environment import SIM_HZ, CompetitionRound, EnvConfig  # noqa: E402

Predict = Callable[[np.ndarray], np.ndarray]

#: Above this, the two paths are not the same policy. Below it, they differ
#: by float32 rounding, which is what torch does and numpy here does not.
SAME_POLICY_TOLERANCE = 1e-4


def observations_from_round(
    policy: Predict, config: EnvConfig, *, seed: int = 1000, seconds: float = 30.0
) -> list[np.ndarray]:
    """Every frame's observation from a round flown by `policy`, decisions held
    for `action_repeat` frames exactly as the evaluator holds them."""
    game = CompetitionRound(config=config, seed=seed)
    observation = game.reset(seed=seed)
    kept = [np.array(observation, dtype=np.float32)]
    held = None
    frames_held = 0
    for _ in range(int(seconds * SIM_HZ)):
        if held is None or frames_held >= config.action_repeat:
            held = policy(observation)
            frames_held = 0
        frames_held += 1
        observation, _, finished, _ = game.step(held)
        kept.append(np.array(observation, dtype=np.float32))
        if finished:
            break
    return kept


def compare(first: Predict, second: Predict, observations: list[np.ndarray]) -> dict[str, object]:
    """Largest and mean absolute difference per action channel, and the
    observation that produced the largest one."""
    if not observations:
        raise ValueError("nothing to compare on")
    differences = np.array(
        [
            np.abs(np.asarray(first(o), dtype=np.float64) - np.asarray(second(o), dtype=np.float64))
            for o in observations
        ]
    )
    worst = int(np.argmax(differences.max(axis=1)))
    return {
        "observations": len(observations),
        "max_abs_per_channel": differences.max(axis=0).tolist(),
        "mean_abs_per_channel": differences.mean(axis=0).tolist(),
        "max_abs": float(differences.max()),
        "worst_index": worst,
        "same_policy": bool(differences.max() <= SAME_POLICY_TOLERANCE),
    }


def describe(result: dict[str, object]) -> str:
    channels = ("aileron", "elevator", "rudder", "throttle")
    lines = [f"{result['observations']} observations, per channel:"]
    for name, worst, mean in zip(
        channels, result["max_abs_per_channel"], result["mean_abs_per_channel"], strict=False
    ):
        lines.append(f"  {name:9} max |diff| {worst:.2e}   mean {mean:.2e}")
    verdict = (
        "same policy: differences are float32 rounding"
        if result["same_policy"]
        else f"NOT the same policy: max |diff| {result['max_abs']:.3g} exceeds {SAME_POLICY_TOLERANCE:g}"
    )
    lines.append(verdict)
    lines.append(
        "兩條推論路徑是同一個策略(只差 float32 捨入)"
        if result["same_policy"]
        else "兩條推論路徑不是同一個策略 —— 池對手飛的不是受測方飛的"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("session", type=Path, help="a session directory with checkpoint.zip and card.json")
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--seed", type=int, default=1000)
    parser.add_argument("--algorithm", choices=["sac"], default="sac")
    args = parser.parse_args(argv)

    import json

    from competition.evaluate import config_from_card, load_policy
    from competition.league import load_actor
    from competition.runtime import StackUnavailable

    card_path = args.session / "card.json"
    card = json.loads(card_path.read_text(encoding="utf-8")) if card_path.is_file() else {}
    try:
        sb3 = load_policy(args.session, args.algorithm)
        numpy_actor = load_actor(args.session / "checkpoint.zip", args.algorithm)
    except StackUnavailable as unavailable:
        print(f"!!  {unavailable}")
        return 1
    config = config_from_card(card, "reference", 1.0)
    observations = observations_from_round(sb3, config, seed=args.seed, seconds=args.seconds)
    result = compare(sb3, numpy_actor, observations)
    print(f"{args.session.name}: SB3 predict vs numpy replay, {args.seconds:.0f} s round, seed {args.seed}")
    print(describe(result))
    return 0 if result["same_policy"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
