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
from competition.features import EXTENDED_STATE_SIZE, STATE_SIZE  # noqa: E402
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

OPPONENT_NAMES = [
    "reference",
    "level",
    "pursuit",
    # The scripted set from competition/adversaries.py, roughly in order of how
    # hard they are to score against.
    "wanderer",
    "scissors",
    "energy",
    "break",
]


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
    #: Our own aircraft was lost. Distinct from the round's EndReason,
    #: which says a crash happened without saying whose.
    blue_crashed: bool = False
    #: Share of frames the ground-avoidance layer had the stick. 0.0 with
    #: no floor. A high number next to a good outcome means the floor flew
    #: the round, not the policy.
    floor_share: float = 0.0
    #: The closest the nose ever came, in degrees, while inside the firing
    #: range. `seconds_in_sweet_spot` says how long the range was right; this
    #: says whether the aim was ever near. Fifty-five seconds at the right
    #: distance and no kills can mean 1.5 degrees off, which is a tuning
    #: problem, or 40 degrees off, which is a different problem entirely, and
    #: nothing measured so far could tell the two apart. 180.0 means the range
    #: was never right at all.
    best_track_angle_deg: float = 180.0
    #: Seconds inside the firing range and within five degrees — twenty-five
    #: times the area of the one-degree cone, so a policy that is trying to
    #: point scores here long before it ever scores a kill.
    seconds_within_5deg: float = 0.0
    #: How fast the aim is moving during the terminal phase, in degrees per
    #: second, averaged over the frames inside the firing range and within
    #: five degrees. Against the decision interval this says whether holding
    #: a one-degree cone is a control problem or a choice: at 10 Hz a rate of
    #: 19 deg/s moves the target 1.9 degrees between decisions, which is wider
    #: than the cone, and no policy can regulate an error it cannot see
    #: between one command and the next.
    track_rate_deg_s: float = 0.0

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
            "best_track_angle_deg": round(self.best_track_angle_deg, 2),
            "seconds_within_5deg": round(self.seconds_within_5deg, 2),
            "track_rate_deg_s": round(self.track_rate_deg_s, 2),
            "floor_share": round(self.floor_share, 4),
            "blue_crashed": self.blue_crashed,
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
        # Our aircraft, not "a crash happened". EndReason.CRASH covers all
        # three cases — we were lost, they were, or both — so against a policy
        # opponent this counted their crashes as ours and reported 90% crashed
        # beside 85% won, which cannot both be true under 表 3.
        #
        # Right for every earlier measurement by luck: the drone flies straight
        # and level and the pursuit controller holds 3,000 ft, so neither ever
        # crashed and the two readings coincided. Self-play is what separated
        # them.
        return self._rate(lambda r: r.blue_crashed)

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
    def mean_attack_seconds(self) -> float:
        """Seconds accumulated inside the one-degree cone, averaged.

        The number the kill threshold is actually compared against, and it had
        been computed every round and shown nowhere. `killed` is this crossing
        3.0, so a board of 0% could mean 2.9 or 0.02 and nothing said which.
        """
        if not self.rounds:
            return 0.0
        # `.get`, because a hand-built outcome in a test carries only the keys
        # that test is about, and a report should not raise over a column.
        return statistics.fmean(_number(r.outcome.blue.get("attack_seconds", 0.0)) for r in self.rounds)

    @property
    def best_attack_seconds(self) -> float:
        """The most any single round accumulated. How close the best case came."""
        if not self.rounds:
            return 0.0
        return max(_number(r.outcome.blue.get("attack_seconds", 0.0)) for r in self.rounds)

    @property
    def mean_track_rate_deg_s(self) -> float:
        """How fast the aim moves in the terminal phase, degrees per second.

        Read against the decision interval. The policies here decide every six
        frames — 0.1 s — so this number divided by ten is how far the target
        travels between one command and the next. More than one degree of that
        and the cone is narrower than the control's own step, which is a
        different problem from a policy that will not hold still.
        """
        rates = [r.track_rate_deg_s for r in self.rounds if r.track_rate_deg_s > 0.0]
        return statistics.fmean(rates) if rates else 0.0

    @property
    def best_track_angle_deg(self) -> float:
        """The closest the nose came in any round, in degrees.

        The best case, not the average, because the question this answers is
        whether the aim is ever nearly right — and one round that got to 1.5
        degrees says something a mean of 40 would bury.
        """
        if not self.rounds:
            return 180.0
        return min(r.best_track_angle_deg for r in self.rounds)

    @property
    def mean_seconds_within_5deg(self) -> float:
        if not self.rounds:
            return 0.0
        return statistics.fmean(r.seconds_within_5deg for r in self.rounds)

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
                f"  within 5 deg   {self.mean_seconds_within_5deg:6.1f} s per round",
                f"  in the cone    {self.mean_attack_seconds:6.2f} s mean, "
                f"{self.best_attack_seconds:.2f} s best (3.00 is a kill)",
                f"  best aim       {self.best_track_angle_deg:6.1f} deg (1.0 is a kill)",
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


def play_round(policy: Policy, config: EnvConfig, seed: int, trace: Any | None = None) -> RoundReport:
    """One round to its end, scored the way the day scores it.

    `trace`, when given, is a `RoundTrace` that gets every frame. Off by
    default: eighteen thousand frames a round is megabytes, and most rounds are
    not worth keeping.
    """
    game = CompetitionRound(config=config, seed=seed)
    observation = game.reset(seed=seed)

    distances: list[float] = []
    sweet_frames = 0
    near_frames = 0
    best_angle = 180.0
    swings: list[float] = []
    previous_angle: float | None = None
    floor_before = 0
    reason = ""
    lo, hi = SWEET_SPOT_M
    envelope = AttackEnvelope()

    while True:
        observation, geometry, finished, reason = game.step(policy(observation))
        distances.append(geometry.distance_m)
        if lo <= geometry.distance_m <= hi:
            sweet_frames += 1
        # Aim is only meaningful where a shot could count, so it is measured
        # against the envelope's own range rather than the narrower band the
        # sweet spot uses for scoring.
        if envelope.min_range_ft <= geometry.distance_ft <= envelope.max_range_ft:
            angle = float(geometry.track_angle_deg)
            best_angle = min(best_angle, angle)
            if angle <= 5.0:
                near_frames += 1
                # Only across consecutive in-close frames: a gap would measure
                # the jump between two separate passes, not how fast the aim
                # moves while it is being held.
                if previous_angle is not None:
                    swings.append(abs(angle - previous_angle) * 60.0)
                previous_angle = angle
            else:
                previous_angle = None
        else:
            previous_angle = None
        if trace is not None:
            trace.record(
                geometry,
                attack_seconds=game.score.attack_seconds,
                g_load=game.own.g_load,
                floor_active=game.floor_frames > floor_before,
            )
            floor_before = game.floor_frames
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
        best_track_angle_deg=best_angle,
        seconds_within_5deg=near_frames / 60.0,
        track_rate_deg_s=statistics.fmean(swings) if swings else 0.0,
        floor_share=game.floor_frames / game.frame if game.frame else 0.0,
        blue_crashed=reason == "CRASH",
    )


def evaluate(
    policy: Policy,
    config: EnvConfig | None = None,
    *,
    rounds: int = 12,
    seed: int = 0,
    label: str = "policy",
    on_round: Callable[[RoundReport], None] | None = None,
    trace_dir: Path | None = None,
) -> Report:
    """`rounds` engagements from consecutive seeds, so two runs are comparable.

    `trace_dir` records every frame of every round into it, one file each, in
    the platform's replay format. Off by default.
    """
    config = config or EnvConfig()
    report = Report(label=label)
    for index in range(rounds):
        trace = _trace_for(trace_dir, label, config, seed + index) if trace_dir else None
        result = None
        if trace is not None:
            trace.open()
        try:
            result = play_round(policy, config, seed + index, trace=trace)
        finally:
            if trace is not None:
                # Closed even when the round raised: a half-written trace of
                # the round that broke is the one most worth having.
                trace.close(
                    outcome=result.outcome.as_dict() if result is not None else {},
                    reason=result.outcome.reason.value if result is not None else "ERROR",
                )
        report.rounds.append(result)
        if on_round is not None:
            on_round(result)
    return report


def _trace_for(trace_dir: Path, label: str, config: EnvConfig, seed: int) -> Any:
    """One trace file per round, named so a directory sorts into an experiment."""
    from competition.trace import RoundTrace

    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)
    trace_dir.mkdir(parents=True, exist_ok=True)
    return RoundTrace(
        trace_dir / f"{safe}-seed{seed}.jsonl.gz",
        label=label,
        opponent=config.opponent,
        seed=seed,
        envelope=config.envelope,
        plant={
            "observation": config.observation,
            "action_repeat": config.action_repeat,
            "rudder_limit": config.rudder_limit,
            "ground_avoidance": config.ground_avoidance is not None,
        },
    )


def load_policy(session_dir: Path, algorithm: str = "sac", device: str = "cpu") -> Policy:
    """A saved session's policy, as a plain function of the observation."""
    from competition.runtime import load_algorithm

    model = load_algorithm(algorithm).load(str(Path(session_dir) / "checkpoint.zip"), device=device)

    def policy(observation: np.ndarray) -> np.ndarray:
        action, _ = model.predict(observation, deterministic=True)
        return np.asarray(action, dtype=np.float64)

    # What the saved network actually takes, so a caller can check it against
    # the plant it is about to build rather than finding out thirty frames of
    # Stable-Baselines3 traceback later.
    policy.observation_width = int(model.observation_space.shape[0])  # type: ignore[attr-defined]
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
        f"{'':22}{'won':>8}{'killed':>9}{'died':>8}{'crashed':>9}"
        f"{'margin':>12}{'152-500m':>10}{'<5deg':>8}{'best':>8}"
        f"{'cone':>8}{'cone+':>8}{'deg/s':>8}{'floor':>8}",
    ]
    for report in reports:
        lines.append(
            f"{report.label[:29]:30}"
            f"{report.win_rate:>7.0%} "
            f"{report.kill_rate:>8.0%} "
            f"{report.death_rate:>7.0%} "
            f"{report.crash_rate:>8.0%} "
            f"{report.mean_margin:>+11,.0f} "
            f"{report.mean_seconds_in_sweet_spot:>9.1f} "
            f"{report.mean_seconds_within_5deg:>7.1f} "
            f"{report.best_track_angle_deg:>7.1f} "
            f"{report.mean_attack_seconds:>7.2f} "
            f"{report.best_attack_seconds:>7.2f} "
            f"{report.mean_track_rate_deg_s:>7.1f} "
            f"{report.mean_floor_share:>7.0%}"
        )
    lines.append("")
    lines.append("  <5deg = seconds in firing range with the nose within 5 degrees.")
    lines.append("  best  = closest the nose ever came, in degrees. A kill needs 1.0 for 3 s.")
    lines.append("  cone  = seconds accumulated inside the one-degree cone, mean and best round.")
    lines.append("          3.00 is a kill. This is the number the threshold compares.")
    lines.append("  deg/s = how fast the aim moves inside five degrees. At a 10 Hz decision")
    lines.append("          rate, over 10 deg/s means the cone is narrower than one command.")
    lines.append("  <5deg = 在射程內、機首偏差五度以內的秒數;best = 機首最接近時的偏差度數。")
    lines.append("  cone / cone+ = 一度錐內的累積秒數,平均與最好的一回合。3.00 就是擊殺。")
    lines.append("  deg/s = 末端瞄準的移動速率。10 Hz 決策下,超過 10 代表錐比一個指令還窄。")
    return "\n".join(lines)


# ------------------------------------------------------------------- the CLI


def config_from_card(
    card: dict[str, Any],
    opponent: str,
    aggression: float,
    ground_avoidance: bool | None = None,
    opponent_policy: Any = None,
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
        opponent_policy=opponent_policy,
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
    parser.add_argument("--opponent", choices=OPPONENT_NAMES, default="reference")
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
    parser.add_argument(
        "--opponent-pool",
        nargs="+",
        default=[],
        metavar="PATH",
        help=(
            "fly against saved policies instead of a built-in opponent, as files "
            "or directories of .zip. Each session meets each of them and gets a "
            "row per pairing. This is the only way to ask whether one generation "
            "beat the last: both built-in opponents are beaten by a centred "
            "stick, so beating them says nothing"
        ),
    )
    parser.add_argument("--algorithm", choices=["sac", "ppo"], default="sac")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--json", type=Path, default=None, help="also write the full detail here")
    parser.add_argument(
        "--trace",
        type=Path,
        default=None,
        metavar="DIR",
        help=(
            "record every frame of every round into DIR, one gzipped JSON Lines "
            "file each, in the platform's replay format. The averages say a "
            "policy reaches the cone and does not stay; only a trace says when, "
            "how often, and what it was doing on the way in"
        ),
    )
    args = parser.parse_args(argv)

    if not args.sessions and not args.baseline:
        parser.error("name at least one session, or pass --baseline on its own")

    # `--opponent-pool` takes many paths, so it swallows a session named after
    # it: `--baseline --opponent-pool v4/checkpoint.zip v5` put v5 in the pool
    # and left nothing under test, and the report that came back compared a
    # centred stick against two opponents while looking exactly like the run
    # that had been asked for.
    swallowed = [
        entry for entry in args.opponent_pool if (Path(entry) / "card.json").is_file() and not args.sessions
    ]
    if swallowed:
        parser.error(
            f"{', '.join(swallowed)} looks like a session to score, not an opponent, "
            "and --opponent-pool has taken it. Sessions go first:\n"
            f"    evaluate {' '.join(swallowed)} --baseline --opponent-pool <checkpoint.zip>"
        )

    # `None` means the built-in opponent named by --opponent. One entry per
    # pairing otherwise, so a session that meets three saved policies gets
    # three rows rather than one average over three different fights.
    foes: list[tuple[str, Any]] = [(args.opponent, None)]
    if args.opponent_pool:
        from competition.league import collect_checkpoints, opponent_from_checkpoint

        foes = [
            (name, opponent_from_checkpoint(path, args.algorithm, args.device))
            for name, path in collect_checkpoints(args.opponent_pool).items()
        ]

    print()
    print(f"Scoring {len(args.sessions)} session(s) over {args.rounds} rounds")
    described_foes = ", ".join(name for name, _ in foes)
    print(f"每個 session 跑同一批 {args.rounds} 個回合(seed {args.seed}),對手 {described_foes}")
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
        config = config_from_card(card, args.opponent, args.opponent_aggression, args.ground_avoidance)
        expected = EXTENDED_STATE_SIZE if config.observation == "extended" else STATE_SIZE
        width = getattr(policy, "observation_width", expected)
        if width != expected:
            # Almost always a missing card.json: the plant falls back to the
            # reference setup, its observation is 10 numbers narrower than the
            # extended one, and SB3 raises about a shape instead of about the
            # file that is not there.
            print()
            print(f"!!  {session.name} was trained on a {width}-number observation, but the")
            print(f"    setup being built for it gives {expected}.")
            if not card:
                print(f"    Its card.json is missing. Copy it next to {session / 'checkpoint.zip'}")
                print("    — the card is what says which observation the policy was trained on.")
            else:
                print(
                    "    Its card.json says "
                    f"observation={config.observation!r}, which does not match the checkpoint."
                )
            print()
            print(f"!!  {session.name} 的觀測寬度是 {width},但要建給它的環境給 {expected}。")
            print("    通常是少了 card.json —— 把它放到 checkpoint.zip 旁邊即可。")
            print()
            return 1

        for foe_name, foe in foes:
            against = f" vs {foe_name}" if foe is not None else ""
            reports.append(
                evaluate(
                    policy,
                    config_from_card(
                        card,
                        args.opponent,
                        args.opponent_aggression,
                        args.ground_avoidance,
                        opponent_policy=foe,
                    ),
                    rounds=args.rounds,
                    seed=args.seed,
                    label=session.name + _floor_suffix(card, args.ground_avoidance) + against,
                    trace_dir=args.trace,
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
        for foe_name, foe in foes:
            against = f" vs {foe_name}" if foe is not None else ""
            reports.append(
                evaluate(
                    neutral_policy(),
                    EnvConfig(
                        opponent=args.opponent,
                        opponent_aggression=args.opponent_aggression,
                        ground_avoidance=GroundAvoidance(**baseline_floor)
                        if baseline_floor is not None
                        else None,
                        opponent_policy=foe,
                    ),
                    rounds=args.rounds,
                    seed=args.seed,
                    label="do nothing" + against,
                    trace_dir=args.trace,
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
