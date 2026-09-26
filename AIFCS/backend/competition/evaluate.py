"""Measure a policy with the judge's ruler, repeatably.

Training reports a reward. The competition reports a verdict, and the two are
not the same function — that is the whole reason `rewards.py` has two entries.
So a number from training cannot answer "is this policy better", and until
there is something that can, every tuning decision is a guess.

This runs whole rounds to their end, scores both sides with `SideScore`, and
calls them with `decide_round` — the organiser's own table. Rounds are seeded
from a base, so two policies run against the same twelve engagements and the
comparison is paired rather than a race between two different dice rolls.

What it deliberately does not do is invent a metric. Everything reported is
either something the rules define (verdict, kill time, the two scores) or a
plain observation (how close, for how long), never a blend of them.
"""

from __future__ import annotations

import statistics
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

# Runnable as a file as well as importable. The path has to be set before the
# package imports below, not inside the __main__ guard underneath them.
_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from competition.action import INITIAL_THROTTLE  # noqa: E402
from competition.environment import CompetitionRound, EnvConfig  # noqa: E402
from competition.runtime import StackUnavailable  # noqa: E402
from competition.scoring import (  # noqa: E402
    AttackEnvelope,
    RoundOutcome,
    Verdict,
    decide_round,
)

FT_PER_M = 1.0 / 0.3048

#: The band that is both a firing solution and the best distance factor.
#: 500 ft is where the attack envelope opens; 500 m is where the 1.2 factor
#: ends. Arithmetic, not judgement — see `docs/COMPETITION.md`.
SWEET_SPOT_M = (AttackEnvelope().min_range_ft * 0.3048, 500.0)

Policy = Callable[[np.ndarray], np.ndarray]


def _number(value: float | bool | None) -> float:
    """A score out of `SideScore.as_dict`, which types its values loosely."""
    if value is None:
        raise ValueError("a score is missing where one is required")
    return float(value)


@dataclass
class RoundReport:
    """One round, as the rules would describe it, plus how it was flown."""

    seed: int
    outcome: RoundOutcome
    frames: int
    min_distance_m: float
    mean_distance_m: float
    seconds_in_sweet_spot: float
    #: Share of frames the ground-avoidance layer had the stick. 0.0 with
    #: no floor. A high number next to a good outcome means the floor flew
    #: the round, not the policy.
    floor_share: float = 0.0

    @property
    def won(self) -> bool:
        return self.outcome.verdict is Verdict.BLUE

    @property
    def killed(self) -> bool:
        return bool(self.outcome.blue["killed"])

    @property
    def was_killed(self) -> bool:
        return bool(self.outcome.red["killed"])

    def as_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "frames": self.frames,
            "min_distance_m": round(self.min_distance_m, 1),
            "mean_distance_m": round(self.mean_distance_m, 1),
            "seconds_in_sweet_spot": round(self.seconds_in_sweet_spot, 2),
            "floor_share": round(self.floor_share, 4),
            **self.outcome.as_dict(),
        }


@dataclass
class Report:
    """Every round, and the handful of rates worth comparing between runs."""

    label: str
    rounds: list[RoundReport] = field(default_factory=list)

    def _rate(self, predicate: Callable[[RoundReport], bool]) -> float:
        return sum(1 for r in self.rounds if predicate(r)) / len(self.rounds) if self.rounds else 0.0

    @property
    def win_rate(self) -> float:
        return self._rate(lambda r: r.won)

    @property
    def kill_rate(self) -> float:
        return self._rate(lambda r: r.killed)

    @property
    def death_rate(self) -> float:
        return self._rate(lambda r: r.was_killed)

    @property
    def crash_rate(self) -> float:
        return self._rate(lambda r: r.outcome.reason.value == "CRASH")

    @property
    def mean_margin(self) -> float:
        """Our advantage score minus theirs, averaged. The tie-breaker on the day."""
        if not self.rounds:
            return 0.0
        return statistics.fmean(
            _number(r.outcome.blue["advantage_score"]) - _number(r.outcome.red["advantage_score"])
            for r in self.rounds
        )

    @property
    def mean_seconds_in_sweet_spot(self) -> float:
        if not self.rounds:
            return 0.0
        return statistics.fmean(r.seconds_in_sweet_spot for r in self.rounds)

    @property
    def mean_floor_share(self) -> float:
        """How much of the round the floor flew, averaged.

        Worth a column of its own because the outcome columns hide it. A
        policy at 0% crashed and 55% won may have earned that, or may have
        been carried: run1 on the corrected floor came back with a margin of
        +1 against +78 on the weaker one, which is what being carried looks
        like.
        """
        if not self.rounds:
            return 0.0
        return statistics.fmean(r.floor_share for r in self.rounds)

    def summary(self) -> str:
        n = len(self.rounds)
        return "\n".join(
            (
                f"{self.label}: {n} rounds",
                f"  won            {self.win_rate:6.1%}  ({sum(r.won for r in self.rounds)}/{n})",
                f"  killed them    {self.kill_rate:6.1%}",
                f"  were killed    {self.death_rate:6.1%}",
                f"  crashed        {self.crash_rate:6.1%}",
                f"  score margin   {self.mean_margin:+,.0f}  (ours minus theirs, mean)",
                f"  in 152-500 m   {self.mean_seconds_in_sweet_spot:6.1f} s per round",
                f"  floor had it   {self.mean_floor_share:6.1%} of frames",
            )
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "rounds": len(self.rounds),
            "win_rate": round(self.win_rate, 4),
            "kill_rate": round(self.kill_rate, 4),
            "death_rate": round(self.death_rate, 4),
            "crash_rate": round(self.crash_rate, 4),
            "mean_margin": round(self.mean_margin, 1),
            "mean_seconds_in_sweet_spot": round(self.mean_seconds_in_sweet_spot, 2),
            "detail": [r.as_dict() for r in self.rounds],
        }


def play_round(policy: Policy, config: EnvConfig, seed: int) -> RoundReport:
    """One round to its end, scored the way the day scores it."""
    game = CompetitionRound(config=config, seed=seed)
    observation = game.reset(seed=seed)

    distances: list[float] = []
    sweet_frames = 0
    reason = ""
    lo, hi = SWEET_SPOT_M

    while True:
        observation, geometry, finished, reason = game.step(policy(observation))
        distances.append(geometry.distance_m)
        if lo <= geometry.distance_m <= hi:
            sweet_frames += 1
        if finished:
            break

    outcome = decide_round(
        game.score,
        game.opponent_score,
        blue_crashed=reason == "CRASH",
        red_crashed=reason == "FOE_CRASH",
        collided=reason == "COLLISION",
    )
    return RoundReport(
        seed=seed,
        outcome=outcome,
        frames=game.frame,
        min_distance_m=min(distances),
        mean_distance_m=statistics.fmean(distances),
        seconds_in_sweet_spot=sweet_frames / 60.0,
        floor_share=game.floor_frames / game.frame if game.frame else 0.0,
    )


def evaluate(
    policy: Policy,
    config: EnvConfig | None = None,
    *,
    rounds: int = 12,
    seed: int = 0,
    label: str = "policy",
    on_round: Callable[[RoundReport], None] | None = None,
) -> Report:
    """`rounds` engagements from consecutive seeds, so two runs are comparable."""
    config = config or EnvConfig()
    report = Report(label=label)
    for index in range(rounds):
        result = play_round(policy, config, seed + index)
        report.rounds.append(result)
        if on_round is not None:
            on_round(result)
    return report


def load_policy(session_dir: Path, algorithm: str = "sac", device: str = "cpu") -> Policy:
    """A saved session's policy, as a plain function of the observation."""
    from competition.runtime import load_algorithm

    model = load_algorithm(algorithm).load(str(Path(session_dir) / "checkpoint.zip"), device=device)

    def policy(observation: np.ndarray) -> np.ndarray:
        action, _ = model.predict(observation, deterministic=True)
        return np.asarray(action, dtype=np.float64)

    return policy


def neutral_policy(throttle: float = INITIAL_THROTTLE) -> Policy:
    """Stick centred, throttle held: the floor any policy has to clear.

    The throttle matters and is easy to get wrong. A four-channel action of all
    zeros is not "do nothing" — the fourth channel is a target, so zero is an
    order to close the throttle, and the first version of this baseline chopped
    the engine and called the resulting crash a floor. Doing nothing means
    changing nothing, so the throttle is held where a round starts it.

    The action is four channels whether or not the rudder is unlocked; that
    flag changes the bounds, not the width.
    """
    command = np.array([0.0, 0.0, 0.0, throttle], dtype=np.float64)

    def policy(observation: np.ndarray) -> np.ndarray:
        return command

    return policy


def compare(reports: Sequence[Report]) -> str:
    """Side by side on the same seeds, which is the only fair way to read them."""
    lines = [
        f"{'':22}{'won':>8}{'killed':>9}{'died':>8}{'crashed':>9}{'margin':>12}{'152-500m':>10}{'floor':>8}",
    ]
    for report in reports:
        lines.append(
            f"{report.label[:21]:22}"
            f"{report.win_rate:>7.0%} "
            f"{report.kill_rate:>8.0%} "
            f"{report.death_rate:>7.0%} "
            f"{report.crash_rate:>8.0%} "
            f"{report.mean_margin:>+11,.0f} "
            f"{report.mean_seconds_in_sweet_spot:>9.1f} "
            f"{report.mean_floor_share:>7.0%}"
        )
    return "\n".join(lines)


# ------------------------------------------------------------------- the CLI


def config_from_card(
    card: dict[str, Any],
    opponent: str,
    aggression: float,
    ground_avoidance: bool | None = None,
) -> EnvConfig:
    """Rebuild the aircraft a session trained on, from what it wrote down.

    Each policy is flown in the plant it learned — its observation, its
    decision rate, its floor, its rudder limit — because on the day that is
    what it will have. Comparing two policies means comparing two *systems*,
    and pretending otherwise would flatter whichever one happens to match
    whatever single configuration the comparison chose.

    The opponent and the round setup are *not* taken from the card. Those are
    the exam, and both candidates have to sit the same one.
    """
    from competition.safety import GroundAvoidance

    described = card.get("environment", {})
    floor = described.get("ground_avoidance")
    if ground_avoidance is True and floor is None:
        # Asking what a policy would do with a floor it never trained under.
        # A fair question to *measure* — the floor is a separate layer and
        # bolting one on needs no retraining — and an unfair one to report
        # without saying so, which the label does.
        floor = {}
    elif ground_avoidance is False:
        floor = None
    return EnvConfig(
        observation=described.get("observation", "reference"),
        action_repeat=int(described.get("action_repeat", 1)),
        rudder_enabled=bool(described.get("rudder_enabled", False)),
        rudder_limit=float(described.get("rudder_limit", 0.2)),
        high_speed_elevator_limit=float(described.get("high_speed_elevator_limit", 0.4)),
        g_limit=described.get("g_limit"),
        speed_before_altitude=bool(described.get("speed_before_altitude", False)),
        # `is not None`, not truthiness: an empty dict means "a floor, with its
        # own defaults", which is exactly what --ground-avoidance asks for on a
        # session that never recorded one. Testing the dict for truth turned
        # that request back into no floor at all.
        ground_avoidance=GroundAvoidance(**floor) if floor is not None else None,
        opponent=opponent,
        opponent_aggression=aggression,
    )


def _floor_suffix(card: dict[str, Any], override: bool | None) -> str:
    """Mark a result the policy did not achieve on its own terms."""
    trained_with = card.get("environment", {}).get("ground_avoidance") is not None
    if override is True and not trained_with:
        return " +floor"
    if override is False and trained_with:
        return " -floor"
    return ""


def _describe(card: dict[str, Any], override: bool | None = None) -> str:
    """Describe the aircraft actually flown, not the one on the card.

    The card says what the policy trained under; --ground-avoidance can
    change what it flies under here. Both lines the report prints have to
    say so, not just the table row: this one is where a reader looks to
    see what the engagement was set up as.
    """
    described = card.get("environment", {})
    parts = [
        f"{card.get('timesteps_done', 0):,} steps",
        str(card.get("reward", "?")),
        str(described.get("observation", "reference")),
        f"{described.get('action_repeat', 1)}x",
    ]
    trained_with = described.get("ground_avoidance") is not None
    if override is True and not trained_with:
        parts.append("floor (borrowed)")
    elif override is False and trained_with:
        parts.append("no floor (removed)")
    elif trained_with and override is not False:
        parts.append("floor")
    return ", ".join(parts)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Score saved sessions with the organiser's own rules, on the same engagements."
    )
    parser.add_argument(
        "sessions",
        nargs="*",
        help="session directories, e.g. models/competition/run1. May be empty with --baseline",
    )
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--seed", type=int, default=1000, help="the same for every session")
    parser.add_argument("--opponent", choices=["reference", "level", "pursuit"], default="reference")
    parser.add_argument("--opponent-aggression", type=float, default=1.0)
    floor = parser.add_mutually_exclusive_group()
    floor.add_argument(
        "--ground-avoidance",
        dest="ground_avoidance",
        action="store_true",
        default=None,
        help="fly every session with the rule-based floor, trained with one or not",
    )
    floor.add_argument(
        "--no-ground-avoidance",
        dest="ground_avoidance",
        action="store_false",
        help="fly every session without it, even those trained with one",
    )
    parser.add_argument(
        "--baseline",
        action="store_true",
        help=(
            "also fly a centred stick on the same seeds. The bar every trained "
            "policy has to clear, and easy to forget it moved: it is flown in "
            "the reference plant with whatever floor this run uses, so changing "
            "the floor changes the bar too"
        ),
    )
    parser.add_argument("--algorithm", choices=["sac", "ppo"], default="sac")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--json", type=Path, default=None, help="also write the full detail here")
    args = parser.parse_args(argv)

    if not args.sessions and not args.baseline:
        parser.error("name at least one session, or pass --baseline on its own")

    print()
    print(f"Scoring {len(args.sessions)} session(s) over {args.rounds} rounds")
    print(f"每個 session 跑同一批 {args.rounds} 個回合(seed {args.seed}),對手 {args.opponent}")
    print()

    reports = []
    for entry in args.sessions:
        session = Path(entry)
        card_path = session / "card.json"
        card = json.loads(card_path.read_text(encoding="utf-8")) if card_path.is_file() else {}
        if not card:
            print(f"  {session.name}: no card.json — assuming the reference setup")

        label = f"{session.name} ({_describe(card, args.ground_avoidance)})" if card else session.name
        print(f"  {label}")
        try:
            policy = load_policy(session, args.algorithm, args.device)
        except StackUnavailable as unavailable:
            print()
            print(f"!!  {unavailable}")
            print()
            return 1
        reports.append(
            evaluate(
                policy,
                config_from_card(card, args.opponent, args.opponent_aggression, args.ground_avoidance),
                rounds=args.rounds,
                seed=args.seed,
                label=session.name + _floor_suffix(card, args.ground_avoidance),
            )
        )

    print()
    if args.baseline:
        # The reference plant, not any session's: a centred stick does not read
        # an observation or use a decision rate, so the only part of a card that
        # would change its flying is the floor, and that comes from the flag.
        from competition.safety import GroundAvoidance

        # Not `floor`: that name is the mutually exclusive argument group a
        # few lines up, and shadowing it type-checked as a group being
        # splatted into GroundAvoidance.
        baseline_floor: dict[str, Any] | None = {} if args.ground_avoidance else None
        described_floor = "floor" if baseline_floor is not None else "no floor"
        print(f"  do nothing (centred stick, reference plant, {described_floor})")
        reports.append(
            evaluate(
                neutral_policy(),
                EnvConfig(
                    opponent=args.opponent,
                    opponent_aggression=args.opponent_aggression,
                    ground_avoidance=GroundAvoidance(**baseline_floor)
                    if baseline_floor is not None
                    else None,
                ),
                rounds=args.rounds,
                seed=args.seed,
                label="do nothing",
            )
        )

    print(compare(reports))
    print()
    best = max(reports, key=lambda report: (report.win_rate, report.mean_margin))
    print(f"  Best on win rate, then margin: {best.label}")
    print(f"  勝率優先、其次分差,最好的是:{best.label}")
    if len(reports) > 1:
        print()
        print("  Same seeds, so this is a paired comparison — the engagements were identical.")
        print("  相同種子,配對比較 —— 兩邊打的是同一批對戰。")

    if args.json is not None:
        args.json.write_text(json.dumps([report.as_dict() for report in reports], indent=2), encoding="utf-8")
        print(f"\n  detail: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
