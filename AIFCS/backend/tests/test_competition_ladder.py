"""A queue of experiments that runs with nobody there (COMP PHASE 17).

The request was "run while my computer is off", five times. The answer is a
rented machine, and on one of those the request changes shape: a box that runs
one experiment and idles bills for the idling. So the unit of work becomes the
queue, and a self-play ladder becomes a file instead of somebody awake at 3am
to start the next generation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from competition.ladder import Step, already_done, missing_opponents, read_plan, run_plan

PLAN = """
steps:
  - name: v6
    flags: --reward shaped --timesteps 2000000
    pool: [v4, v5]
  - name: v7
    flags: --reward shaped --timesteps 2000000
    pool: [v4, v5, v6]
"""


def _session(root: Path, name: str, done: int = 0, target: int = 0, checkpoint: bool = True):
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    if target:
        (folder / "state.json").write_text(
            json.dumps({"timesteps_done": done, "target_timesteps": target}), encoding="utf-8"
        )
    if checkpoint:
        (folder / "checkpoint.zip").write_bytes(b"")
    return folder


# ------------------------------------------------------------------ the plan


def test_a_ladder_is_a_file(tmp_path):
    path = tmp_path / "ladder.yaml"
    path.write_text(PLAN, encoding="utf-8")

    steps = read_plan(path)

    assert [step.name for step in steps] == ["v6", "v7"]
    assert steps[1].pool == ["v4", "v5", "v6"], "a step can fight what the one before it built"


def test_two_steps_of_one_name_is_refused(tmp_path):
    """The second would train the same session and then be skipped as already
    finished, so the plan would quietly do half of what it says."""
    path = tmp_path / "ladder.yaml"
    path.write_text("steps:\n  - name: v6\n  - name: v6\n", encoding="utf-8")

    with pytest.raises(ValueError, match="two steps called v6"):
        read_plan(path)


def test_a_step_with_no_name_is_refused(tmp_path):
    path = tmp_path / "ladder.yaml"
    path.write_text("steps:\n  - flags: --timesteps 100\n", encoding="utf-8")

    with pytest.raises(ValueError, match="step 1"):
        read_plan(path)


# ------------------------------------------------------- what a step runs as


def test_the_pool_is_named_by_session_and_resolved_to_checkpoints(tmp_path):
    """A plan reads as a ladder; the directory layout stays out of it."""
    step = Step(name="v6", flags="--reward shaped", pool=["v4", "v5"])

    command = step.train_command("python", tmp_path)

    assert "--opponent-pool" in command
    assert command[command.index("--opponent-pool") + 1].endswith("v4/checkpoint.zip")
    assert "--reward" in command and "shaped" in command


def test_a_step_with_no_pool_does_not_pass_an_empty_flag(tmp_path):
    command = Step(name="v6").train_command("python", tmp_path)

    assert "--opponent-pool" not in command


def test_scoring_a_step_puts_it_against_its_own_opponents(tmp_path):
    """Beating the built-in drone says nothing — a centred stick does that 65%
    of the time. What a generation is measured against is what it trained
    against."""
    command = Step(name="v6", pool=["v4"]).evaluate_command("python", tmp_path)

    assert "--baseline" in command
    assert "--opponent-pool" in command


# --------------------------------------------------------- skipping and order


def test_a_finished_session_is_skipped(tmp_path):
    """Rerunning a plan is how a dead machine is recovered, so a plan has to be
    safe to run twice."""
    _session(tmp_path, "v6", done=2_000_000, target=2_000_000)

    assert already_done(Step(name="v6"), tmp_path) is True


def test_a_part_finished_session_is_not_skipped(tmp_path):
    """It resumes instead, which is what train.py --name already does."""
    _session(tmp_path, "v6", done=350_000, target=2_000_000)

    assert already_done(Step(name="v6"), tmp_path) is False


def test_a_session_finished_by_hand_outside_the_plan_still_counts(tmp_path):
    """Read from the session's own state, not from a marker the queue wrote."""
    _session(tmp_path, "v6", done=5_000_000, target=2_000_000)

    assert already_done(Step(name="v6"), tmp_path) is True


def test_a_pool_member_that_does_not_exist_is_caught_before_training(tmp_path):
    """A typo in a pool name costs the whole step if it is found afterwards."""
    _session(tmp_path, "v4")

    assert missing_opponents(Step(name="v6", pool=["v4", "v5"]), tmp_path) == ["v5"]


# ------------------------------------------------------------- running it all


def test_the_queue_carries_on_past_a_step_that_fails(tmp_path):
    """Eight hours of ladder should not be lost to one bad flag in step two."""
    _session(tmp_path, "v4")
    calls: list[list[str]] = []

    def runner(command):
        calls.append(command)
        return 1 if "v6" in command else 0

    results = run_plan(
        [Step(name="v6", pool=["v4"]), Step(name="v7", pool=["v4"])],
        tmp_path,
        "python",
        runner=runner,
    )

    assert [r["outcome"] for r in results] == ["train failed", "done"]
    assert any("v7" in command for command in calls), "the next step still ran"


def test_a_failed_train_does_not_go_on_to_score_nothing(tmp_path):
    _session(tmp_path, "v4")
    phases: list[str] = []

    def runner(command):
        phases.append("evaluate" if "competition.evaluate" in command else "train")
        return 1

    run_plan([Step(name="v6", pool=["v4"])], tmp_path, "python", runner=runner)

    assert phases == ["train"]


def test_a_blocked_step_runs_nothing_at_all(tmp_path):
    calls: list[list[str]] = []
    results = run_plan(
        [Step(name="v6", pool=["nonexistent"])],
        tmp_path,
        "python",
        runner=lambda command: calls.append(command) or 0,
    )

    assert results[0]["outcome"] == "blocked"
    assert calls == []
