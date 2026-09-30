"""Run a whole queue of experiments without anyone there.

Written because the answer to "can it run while my laptop is off" is a rented
machine, and on a rented machine the interesting question changes. A box that
runs one experiment and then sits idle bills for the idling; a box that works
through a list and shuts itself off bills for the work. The two requests —
run while I am away, and run the next thing automatically — are the same
feature once the machine is not the one on your desk.

A plan is a YAML file of steps. Each step trains a session and then scores it,
and a step can name earlier sessions as its opponents, which is what makes a
self-play ladder expressible as a file rather than as someone awake at 3am:

    steps:
      - name: v6
        flags: --reward shaped --rudder-limit 0.6 --timesteps 2000000
        pool: [v4, v5]
      - name: v7
        flags: --reward shaped --rudder-limit 0.6 --timesteps 2000000
        pool: [v4, v5, v6]

Rerunning the same plan is safe and is the intended way to recover: a step
whose session has already reached its target is skipped, and a step that was
part-way through resumes, because that is what `train.py --name` does. A
machine that dies at step three is restarted by running the same command.

Each step is a subprocess. A step that crashes is recorded and the queue
carries on, because eight hours of ladder should not be lost to one bad flag
in step four.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_ROOT = Path("models/competition")

#: What train.py returns when a run was stopped rather than finished — the
#: shell convention for "the person interrupted this".
INTERRUPTED = 130


@dataclass
class Step:
    """One experiment: train it, then score it."""

    name: str
    flags: str = ""
    pool: list[str] = field(default_factory=list)
    evaluate: bool = True

    def scripted(self) -> list[str]:
        """The pool entries that are scripted opponents, not sessions."""
        from competition.environment import opponent_names

        names = set(opponent_names())
        return [name for name in self.pool if name in names]

    def sessions(self) -> list[str]:
        """The pool entries that have to exist as sessions under the root."""
        scripted = set(self.scripted())
        return [name for name in self.pool if name not in scripted]

    def pool_paths(self, root: Path) -> list[str]:
        """Opponent checkpoints, by session name.

        Named rather than pathed so a plan reads as a ladder and does not
        repeat the directory layout on every line. Scripted names are not
        paths and are not here; `train_command` passes them as themselves.
        """
        return [str(root / name / "checkpoint.zip") for name in self.sessions()]

    def train_command(self, python: str, root: Path) -> list[str]:
        command = [python, "-m", "competition.train", "--name", self.name]
        command += self.flags.split()
        if self.pool:
            command += ["--opponent-pool", *self.scripted(), *self.pool_paths(root)]
        return command

    def evaluate_command(self, python: str, root: Path) -> list[str]:
        command = [python, "-m", "competition.evaluate", str(root / self.name), "--baseline"]
        if "--ground-avoidance" in self.flags.split():
            # The baseline row is a centred stick, and a centred stick with no
            # floor flies into the ground: every `do nothing` row in the v6
            # report read 100% crashed and -139 margin, which is not the bar a
            # trained policy has to clear — the measured bar is 65% and +51,
            # with the floor. The step trained under a floor, so the step is
            # scored under one, or its own report compares two different
            # aeroplanes.
            command.append("--ground-avoidance")
        if self.pool_paths(root):
            # The scoreboard already fights the scripted set; the pool row is
            # for the saved policies the step trained against.
            command += ["--opponent-pool", *self.pool_paths(root)]
        return command


def read_plan(path: Path) -> list[Step]:
    """The steps in a plan file, in order."""
    import yaml

    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    steps = document.get("steps")
    if not steps:
        raise ValueError(f"{path} has no steps")

    out: list[Step] = []
    seen: set[str] = set()
    for index, entry in enumerate(steps, start=1):
        name = str(entry.get("name", "")).strip()
        if not name:
            raise ValueError(f"step {index} in {path} has no name")
        if name in seen:
            # Two steps of one name would train the same session twice and the
            # second would be skipped as already finished, silently.
            raise ValueError(f"{path} has two steps called {name}")
        seen.add(name)
        out.append(
            Step(
                name=name,
                flags=str(entry.get("flags", "")),
                pool=[str(item) for item in entry.get("pool", [])],
                evaluate=bool(entry.get("evaluate", True)),
            )
        )
    return out


def already_done(step: Step, root: Path) -> bool:
    """Has this session already reached the target it was given?

    Read from the session's own state, not from a marker this wrote, so a run
    finished by hand outside the plan also counts.
    """
    state_path = root / step.name / "state.json"
    if not state_path.is_file():
        return False
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    target = int(state.get("target_timesteps", 0))
    return target > 0 and int(state.get("timesteps_done", 0)) >= target


def missing_opponents(step: Step, root: Path, coming: set[str] | None = None) -> list[str]:
    """Pool members that neither exist yet nor are built by an earlier step.

    Checked before training rather than after eight hours of it: a plan that
    names a session it never builds is a typo, and the cost of finding out
    late is the whole step.

    `coming` is what the steps before this one will have produced by the time
    it runs, and leaving it out is what made the first dry run useless: a
    ladder is *supposed* to fight what the step before it built, so a plan
    whose every rung depends on the previous one reported every rung after the
    first as blocked. In a real run they would all have been there.
    """
    built = coming or set()
    return [
        name
        for name in step.sessions()
        if name not in built and not (root / name / "checkpoint.zip").is_file()
    ]


def run_plan(
    steps: list[Step],
    root: Path,
    python: str,
    dry_run: bool = False,
    runner: Any = None,
) -> list[dict[str, Any]]:
    """Work through the steps. Returns what happened to each."""
    runner = runner or (lambda command: subprocess.call(command))
    results: list[dict[str, Any]] = []

    # What the steps above this one will have produced by the time it runs.
    coming: set[str] = set()

    for position, step in enumerate(steps, start=1):
        header = f"[{position}/{len(steps)}] {step.name}"
        coming.add(step.name)

        if already_done(step, root):
            print(f"{header}: already at its target, skipping", flush=True)
            results.append({"name": step.name, "outcome": "skipped"})
            continue

        absent = missing_opponents(step, root, coming - {step.name})
        if absent:
            print(f"{header}: needs {', '.join(absent)}, which do not exist yet", flush=True)
            results.append({"name": step.name, "outcome": "blocked", "missing": absent})
            continue

        commands = [("train", step.train_command(python, root))]
        if step.evaluate:
            commands.append(("evaluate", step.evaluate_command(python, root)))

        # "done" would be a lie on a dry run, and the kind that reads as a
        # green plan.
        outcome = "would run" if dry_run else "done"
        stop_the_plan = False
        started = time.perf_counter()
        for phase, command in commands:
            print(f"{header}: {phase}", flush=True)
            print(f"    {' '.join(command)}", flush=True)
            if dry_run:
                continue
            code = runner(command)
            if code == INTERRUPTED:
                # Somebody asked for this step to stop — a STOP file or Ctrl+C.
                # "Carry on" is the wrong reading of that: stopping v7 and
                # getting v8 started in its place is the opposite of what the
                # person pressing the key wanted. The step's checkpoint is
                # saved, so rerunning the plan resumes it.
                print(f"{header}: {phase} was stopped — abandoning the rest of the plan", flush=True)
                outcome = "stopped"
                stop_the_plan = True
                break
            if code != 0:
                # One bad step does not cost the rest of the night.
                print(f"{header}: {phase} exited {code} — carrying on", flush=True)
                outcome = f"{phase} failed"
                break

        results.append(
            {"name": step.name, "outcome": outcome, "seconds": round(time.perf_counter() - started, 1)}
        )
        if stop_the_plan:
            break

    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a queue of experiments unattended.")
    parser.add_argument("plan", help="a YAML plan, e.g. configs/ladder.yaml")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--dry-run", action="store_true", help="print the commands and stop")
    args = parser.parse_args(argv)

    steps = read_plan(Path(args.plan))
    print()
    print(f"{len(steps)} step(s) from {args.plan}")
    print()

    results = run_plan(steps, args.root, args.python, dry_run=args.dry_run)

    print()
    print("  step                 outcome")
    print("  " + "-" * 44)
    for result in results:
        print(f"  {result['name'][:20]:20} {result['outcome']}")
    print()
    if any(r["outcome"] == "stopped" for r in results):
        print("  Stopped. Run the same command again to carry on from here.")
        print("  已停止。再跑一次同樣的指令就會從這裡接續。")
        print()
        return INTERRUPTED
    return 0 if all(r["outcome"] in ("done", "skipped", "would run") for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
