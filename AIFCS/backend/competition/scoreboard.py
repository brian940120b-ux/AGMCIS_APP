"""Every session against the same fixed opponents. One table, one command.

The ruler problem, stated: until now "this generation beats the last" rested on
v4 and v5, two learned opponents that move whenever they are retrained, one of
which a centred stick beats 80% of the time. Marks on a ruler are not supposed
to move, and one of ours was in the wrong place.

So: the scripted set from `competition.adversaries`, which cannot drift, plus
the built-in reference and a centred stick for the floor. Every session meets
every one of them on the same seeds. What comes out is comparable across weeks
and across machines, which is the whole job.

    python -m competition.scoreboard models/competition/v6 models/competition/v7p

It is slow — sessions times opponents times rounds, each 300 seconds of flight
— and that is the cost of a number worth trusting. `--rounds` trades it off.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # pragma: no cover - runnable as a file
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from competition.adversaries import ADVERSARIES
from competition.environment import EnvConfig
from competition.evaluate import (
    Report,
    config_from_card,
    evaluate,
    load_policy,
    neutral_policy,
)
from competition.runtime import StackUnavailable
from competition.safety import GroundAvoidance

#: The fixed bench, easiest first. `reference` is the organiser's own drone and
#: stays because it is the one opponent we know the host also has; the rest are
#: ours and are scripted precisely so they never change.
BENCH: tuple[str, ...] = ("reference", "pursuit", *reversed(list(ADVERSARIES)))


def score_session(
    session: Path | None,
    *,
    bench: tuple[str, ...],
    rounds: int,
    seed: int,
    algorithm: str,
    device: str,
    ground_avoidance: bool,
) -> dict[str, Report]:
    """One session against each opponent on the bench.

    `session` of None is the centred stick, which is the floor every one of
    these has to clear and is worth a row of its own on every board.
    """
    if session is None:
        policy = neutral_policy()
        card: dict[str, Any] = {}
    else:
        card_path = session / "card.json"
        card = json.loads(card_path.read_text(encoding="utf-8")) if card_path.is_file() else {}
        policy = load_policy(session, algorithm, device)

    out: dict[str, Report] = {}
    for opponent in bench:
        if session is None:
            # The reference plant: a centred stick reads no observation and has
            # no decision rate, so the only part of a card that would change
            # its flying is the floor.
            config = EnvConfig(
                opponent=opponent,
                ground_avoidance=GroundAvoidance() if ground_avoidance else None,
            )
        else:
            config = config_from_card(card, opponent, 1.0, ground_avoidance or None)
        who = session.name if session is not None else "do nothing"
        out[opponent] = evaluate(policy, config, rounds=rounds, seed=seed, label=f"{who} vs {opponent}")
    return out


def table(board: dict[str, dict[str, Report]], bench: tuple[str, ...], metric: str) -> str:
    """One metric across the whole board, sessions down, opponents across."""
    pick = {
        "won": lambda r: f"{r.win_rate:.0%}",
        "margin": lambda r: f"{r.mean_margin:+,.0f}",
        "cone": lambda r: f"{r.best_attack_seconds:.2f}",
        "killed": lambda r: f"{r.kill_rate:.0%}",
    }[metric]

    width = max((len(name) for name in board), default=10) + 2
    lines = [f"{metric:<{width}}" + "".join(f"{o[:9]:>11}" for o in bench)]
    lines.append("-" * (width + 11 * len(bench)))
    for name, rows in board.items():
        lines.append(
            f"{name:<{width}}" + "".join(f"{pick(rows[o]):>11}" if o in rows else f"{'-':>11}" for o in bench)
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("sessions", nargs="*", type=Path, help="session directories")
    parser.add_argument("--rounds", type=int, default=6)
    parser.add_argument("--seed", type=int, default=1000)
    parser.add_argument("--algorithm", choices=["sac", "ppo"], default="sac")
    parser.add_argument("--device", default="cpu")
    parser.add_argument(
        "--no-baseline",
        action="store_true",
        help="leave the centred stick off. It is the floor, so normally it stays",
    )
    parser.add_argument(
        "--no-ground-avoidance",
        action="store_true",
        help="fly everything without the floor layer, including the baseline",
    )
    parser.add_argument("--json", type=Path, default=None, help="also write the full detail here")
    args = parser.parse_args(argv)

    bench = BENCH
    print()
    print(f"{len(args.sessions)} session(s) x {len(bench)} opponents x {args.rounds} rounds")
    print(f"每個 session 對 {len(bench)} 個固定對手各跑 {args.rounds} 回合(seed {args.seed})")
    print(f"bench: {', '.join(bench)}")
    print()

    board: dict[str, dict[str, Report]] = {}
    targets: list[Path | None] = list(args.sessions)
    if not args.no_baseline:
        targets.append(None)

    for session in targets:
        name = session.name if session is not None else "do nothing"
        print(f"  {name} ...", flush=True)
        try:
            board[name] = score_session(
                session,
                bench=bench,
                rounds=args.rounds,
                seed=args.seed,
                algorithm=args.algorithm,
                device=args.device,
                ground_avoidance=not args.no_ground_avoidance,
            )
        except StackUnavailable as unavailable:
            print(f"!!  {unavailable}")
            return 1

    print()
    for metric in ("won", "margin", "cone", "killed"):
        print(table(board, bench, metric))
        print()
    print("  cone = best round's accumulated seconds inside the one-degree cone. 3.00 is a kill.")
    print("  每個對手都是腳本寫死的,不會隨訓練漂移 —— 這是一把不會動的尺。")
    print()

    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(
                {name: {o: r.as_dict() for o, r in rows.items()} for name, rows in board.items()},
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"  detail: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
