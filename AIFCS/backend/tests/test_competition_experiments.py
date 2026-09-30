"""Experiments, written down (brief PART 15).

A session card says what the plant was. It cannot say what the run was for or
what was concluded — v7p's hypothesis and its refutation live in a chat log.
These pin that a record holds all nine answers and that the trainer reads the
same file the person declared.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from competition import experiments


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(experiments, "EXPERIMENTS_DIR", tmp_path / "experiments")
    monkeypatch.setattr(experiments, "current_commit", lambda: "abc1234")
    return tmp_path


def test_a_record_holds_the_nine_answers(sandbox: Path):
    record = experiments.new(
        "EXP-T1",
        question="why",
        hypothesis="because",
        session="s1",
        flags="--reward shaped --timesteps 10",
        pool=["v4"],
    )
    back = experiments.Experiment.load("EXP-T1")

    assert back.question == "why"
    assert back.hypothesis == "because"
    assert back.session == "s1"
    assert back.train_flags() == ["--reward", "shaped", "--timesteps", "10"]
    assert back.pool == ["v4"]
    assert back.git_commit == "abc1234", "reproducibility needs the commit"
    assert back.status == "PLANNED"
    assert record.path.is_file()


def test_no_reward_flag_is_the_organisers_own_tier(sandbox: Path):
    """train.py's default reward is `reference`, the organiser's. A record with
    no --reward is therefore an official-tier run, and has to say so."""
    assert experiments.reward_tier_of("--timesteps 2000000") == "official"
    assert experiments.reward_tier_of("--reward shaped") == "research"
    assert experiments.reward_tier_of("--reward=pointed") == "experimental"
    assert experiments.reward_tier_of("--reward margin") == "official-derived"
    assert experiments.reward_tier_of("--reward nonsense") == "unknown"


def test_every_reward_mode_has_a_tier():
    """A new reward added to the enum without a tier would silently read as
    unknown on every record that used it."""
    from competition.rewards import RewardMode

    for mode in RewardMode:
        assert mode.value in experiments.REWARD_TIERS, f"{mode.value} has no tier"


def test_the_same_id_cannot_be_declared_twice(sandbox: Path):
    experiments.new("EXP-T2", question="q", hypothesis="h", session="s")
    with pytest.raises(FileExistsError):
        experiments.new("EXP-T2", question="q2", hypothesis="h2", session="s2")


def test_a_result_needs_a_decision_from_the_fixed_list(sandbox: Path):
    experiments.new("EXP-T3", question="q", hypothesis="h", session="s")
    with pytest.raises(ValueError):
        experiments.record_result("EXP-T3", decision="maybe")


def test_a_result_carries_the_scoreboard_headline_not_the_detail(sandbox: Path):
    """The JSON stays where it is; the record points at it and keeps the four
    numbers a decision is made on."""
    experiments.new("EXP-T4", question="q", hypothesis="h", session="s")
    board = sandbox / "board.json"
    board.write_text(
        json.dumps(
            {
                "s": {
                    "break": {
                        "win_rate": 0.5,
                        "mean_margin": 12.0,
                        "best_attack_seconds": 0.7,
                        "kill_rate": 0.0,
                        "detail": [{"seed": 1}] * 20,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    record = experiments.record_result("EXP-T4", scoreboard=board, decision="reject", notes="n")

    assert record.status == "DONE"
    assert record.decision == "reject"
    row = record.results["summary"]["s"]["break"]
    assert row == {"won": 0.5, "margin": 12.0, "cone_best": 0.7, "killed": 0.0}
    assert "detail" not in json.dumps(record.results)


def test_marking_running_keeps_the_first_start_time(sandbox: Path):
    experiments.new("EXP-T5", question="q", hypothesis="h", session="s")
    first = experiments.mark_running("EXP-T5").started_at
    again = experiments.mark_running("EXP-T5").started_at

    assert first == again, "a resumed run is the same run"


def test_the_shipped_baseline_record_is_the_organisers_recipe():
    """EXP-001 is the brief's OFFICIAL_SAC_BASELINE. Any flag beyond the step
    count would make it something else."""
    record = experiments.Experiment.load("EXP-001-official-sac-baseline")

    assert record.reward_tier == "official"
    assert record.train_flags() == ["--timesteps", "2000000"]
    assert record.pool == []
