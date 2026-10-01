"""An experiment is a question, a change, a run and a decision — written down.

Until now a training run left a session directory with a card.json in it, and
the reason it was run lived in a chat transcript. The card can say what the
plant was; it cannot say what the run was *for*, what it was expected to show,
or what was concluded from it. v7p is the case in point: it exists because of
a hypothesis about reward gradient near the cone, that hypothesis was wrong,
and the only record of either is a conversation.

So: one YAML file per experiment, under `experiments/`, that a run is started
*from* and a result is written *back to*. The ladder plan and the Kaggle
trainer read the same file, so the thing that ran is the thing that was
declared. Each record answers the nine questions in the brief — why, what
changed, what data, what model, how trained, how evaluated, result,
reproducible, keep.

    python -m competition.experiments new EXP-002 --question "..." \\
        --hypothesis "..." --flags "--reward shaped ..." --pool v4 v5
    python -m competition.experiments show EXP-001
    python -m competition.experiments result EXP-001 --scoreboard results.json \\
        --decision keep --notes "..."
    python -m competition.experiments list

Status moves PLANNED -> RUNNING -> DONE, and DONE carries a decision of keep,
reject or inconclusive. Nothing here promotes a model: that gate is the
registry's, and a result written here is evidence for it, not a verdict.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # pragma: no cover - runnable as a file
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml

#: Relative to the AIFCS root, which is the directory above `backend/`.
EXPERIMENTS_DIR = Path(__file__).resolve().parents[2] / "experiments"

STATUSES = ("PLANNED", "RUNNING", "DONE")

#: What an empty flag string means, said once.
DEFAULT_FLAGS_NOTE = "(train.py defaults: the organiser's recipe)"
DECISIONS = ("keep", "reject", "inconclusive")

#: Which tier a reward belongs to. The brief (PART 13) wants official, research
#: and experimental rewards kept apart, and a record has to say which it used.
REWARD_TIERS: dict[str, str] = {
    "reference": "official",  # the organiser's own training reward, unchanged
    "score": "official-derived",  # the competition score, used as-is
    "margin": "official-derived",  # the score minus the opponent's
    "shaped": "research",  # margin plus tracking/range/deck shaping (v6)
    "pointed": "experimental",  # shaped plus the steep near-cone term (v7p)
    "potential": "research",  # margin plus shaped's tracking term as a potential difference (H4)
}


@dataclass
class Experiment:
    """One record. Field names are the file's keys."""

    id: str
    question: str
    hypothesis: str
    session: str
    flags: str = ""
    pool: list[str] = field(default_factory=list)
    reward_tier: str = "unknown"
    status: str = "PLANNED"
    created_at: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    git_commit: str | None = None
    results: dict[str, Any] = field(default_factory=dict)
    decision: str | None = None
    notes: str = ""

    @property
    def path(self) -> Path:
        return EXPERIMENTS_DIR / f"{self.id}.yaml"

    def save(self) -> Path:
        EXPERIMENTS_DIR.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            yaml.safe_dump(asdict(self), sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        return self.path

    @classmethod
    def load(cls, experiment_id: str) -> Experiment:
        path = EXPERIMENTS_DIR / f"{experiment_id}.yaml"
        if not path.is_file():
            raise FileNotFoundError(f"no experiment {experiment_id!r} in {EXPERIMENTS_DIR}")
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls(**data)

    def train_flags(self) -> list[str]:
        """The command-line tail that reproduces this run's training."""
        return self.flags.split()


def reward_tier_of(flags: str) -> str:
    """Read `--reward X` out of a flag string and name its tier.

    No `--reward` means train.py's default, which is the organiser's own.
    """
    tokens = flags.split()
    for index, token in enumerate(tokens):
        if token == "--reward" and index + 1 < len(tokens):
            return REWARD_TIERS.get(tokens[index + 1], "unknown")
        if token.startswith("--reward="):
            return REWARD_TIERS.get(token.split("=", 1)[1], "unknown")
    return REWARD_TIERS["reference"]


def current_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=EXPERIMENTS_DIR.parent,
            check=False,
        )
    except OSError:
        return None
    return out.stdout.strip() or None


def new(
    experiment_id: str,
    *,
    question: str,
    hypothesis: str,
    session: str,
    flags: str = "",
    pool: list[str] | None = None,
    notes: str = "",
) -> Experiment:
    if (EXPERIMENTS_DIR / f"{experiment_id}.yaml").exists():
        raise FileExistsError(f"{experiment_id} already exists; pick a new id")
    record = Experiment(
        id=experiment_id,
        question=question,
        hypothesis=hypothesis,
        session=session,
        flags=flags,
        pool=list(pool or []),
        reward_tier=reward_tier_of(flags),
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        git_commit=current_commit(),
        notes=notes,
    )
    record.save()
    return record


def mark_running(experiment_id: str) -> Experiment:
    record = Experiment.load(experiment_id)
    record.status = "RUNNING"
    record.started_at = record.started_at or datetime.now(UTC).isoformat(timespec="seconds")
    record.git_commit = record.git_commit or current_commit()
    record.save()
    return record


def record_result(
    experiment_id: str,
    *,
    scoreboard: Path | None = None,
    decision: str,
    notes: str = "",
) -> Experiment:
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}, not {decision!r}")
    record = Experiment.load(experiment_id)
    if scoreboard is not None:
        data = json.loads(Path(scoreboard).read_text(encoding="utf-8"))
        # Keep the headline rows, not the per-round detail: the JSON file stays
        # where it is and the record points at it. Merged, not assigned: a
        # record that already carries Kaggle or paired-evaluate results keeps
        # them, and the first version of this line threw them away.
        record.results.update(
            scoreboard=str(scoreboard),
            summary={
                name: {
                    opponent: {
                        "won": rows.get("win_rate"),
                        "margin": rows.get("mean_margin"),
                        "cone_best": rows.get("best_attack_seconds"),
                        "killed": rows.get("kill_rate"),
                    }
                    for opponent, rows in board.items()
                }
                for name, board in data.items()
            },
        )
    record.status = "DONE"
    record.decision = decision
    record.finished_at = datetime.now(UTC).isoformat(timespec="seconds")
    if notes:
        record.notes = (record.notes + "\n" + notes).strip()
    record.save()
    return record


def list_all() -> list[Experiment]:
    if not EXPERIMENTS_DIR.is_dir():
        return []
    out = []
    for path in sorted(EXPERIMENTS_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        out.append(Experiment(**data))
    return out


def describe(record: Experiment) -> str:
    lines = [
        f"{record.id}  [{record.status}]" + (f"  decision: {record.decision}" if record.decision else ""),
        f"  question:   {record.question}",
        f"  hypothesis: {record.hypothesis}",
        f"  session:    {record.session}",
        f"  reward:     {record.reward_tier}",
        f"  flags:      {record.flags or DEFAULT_FLAGS_NOTE}",
        f"  pool:       {', '.join(record.pool) or '(none: built-in opponent)'}",
        f"  commit:     {record.git_commit or '?'}",
    ]
    if record.results.get("summary"):
        lines.append("  results:")
        for name, board in record.results["summary"].items():
            for opponent, row in board.items():
                won = row.get("won")
                lines.append(
                    f"    {name} vs {opponent:<10} won {won:>5.0%}  margin {row.get('margin', 0):+,.0f}"
                    f"  cone+ {row.get('cone_best', 0):.2f}"
                    if isinstance(won, (int, float))
                    else f"    {name} vs {opponent}: {row}"
                )
    if record.notes:
        lines.append("  notes:")
        lines.extend(f"    {line}" for line in record.notes.splitlines())
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Experiments, written down.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_new = sub.add_parser("new", help="declare an experiment before running it")
    p_new.add_argument("id")
    p_new.add_argument("--question", required=True)
    p_new.add_argument("--hypothesis", required=True)
    p_new.add_argument("--session", required=True, help="session directory name it will train")
    p_new.add_argument("--flags", default="", help="train.py flags; empty = organiser defaults")
    p_new.add_argument("--pool", nargs="*", default=[])
    p_new.add_argument("--notes", default="")

    p_show = sub.add_parser("show")
    p_show.add_argument("id")

    p_run = sub.add_parser("running", help="mark it started")
    p_run.add_argument("id")

    p_res = sub.add_parser("result", help="write the outcome and a decision")
    p_res.add_argument("id")
    p_res.add_argument("--scoreboard", type=Path, default=None, help="scoreboard --json output")
    p_res.add_argument("--decision", choices=DECISIONS, required=True)
    p_res.add_argument("--notes", default="")

    sub.add_parser("list")

    args = parser.parse_args(argv)
    if args.command == "new":
        record = new(
            args.id,
            question=args.question,
            hypothesis=args.hypothesis,
            session=args.session,
            flags=args.flags,
            pool=args.pool,
            notes=args.notes,
        )
        print(f"wrote {record.path}")
        print(describe(record))
    elif args.command == "show":
        print(describe(Experiment.load(args.id)))
    elif args.command == "running":
        print(describe(mark_running(args.id)))
    elif args.command == "result":
        record = record_result(args.id, scoreboard=args.scoreboard, decision=args.decision, notes=args.notes)
        print(describe(record))
    elif args.command == "list":
        records = list_all()
        if not records:
            print(f"no experiments in {EXPERIMENTS_DIR}")
        for record in records:
            print(f"{record.id:<28} {record.status:<8} {record.decision or '':<13} {record.session}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
