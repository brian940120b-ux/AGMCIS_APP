"""The trainer end to end, at the smallest size that still exercises it.

Everything here is about the promise that stopping costs almost nothing: a run
writes a session, a second run finds it and carries on. That is the feature the
overnight runs depend on, and it had no test that actually ran the trainer.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("jsbsim")
pytest.importorskip("gymnasium")
pytest.importorskip("stable_baselines3")

TRAIN = Path(__file__).resolve().parents[1] / "competition" / "train.py"


def run_trainer(output: Path, timesteps: int) -> str:
    """One training run, small enough to be a test and real enough to mean something."""
    result = subprocess.run(
        [
            sys.executable,
            str(TRAIN),
            "--name",
            "t",
            "--algorithm",
            "sac",
            "--timesteps",
            str(timesteps),
            "--workers",
            "1",
            "--device",
            "cpu",
            "--no-save-buffer",
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_a_run_can_be_stopped_and_carried_on(tmp_path: Path):
    first = run_trainer(tmp_path, 400)
    assert "starting t: 400 steps" in first

    state = json.loads((tmp_path / "t" / "state.json").read_text(encoding="utf-8"))
    assert state["timesteps_done"] >= 400

    second = run_trainer(tmp_path, 800)
    assert "resuming t:" in second, "a second run must find the first one's work"
    assert "training 400 more steps" in second, "--timesteps is a total, not an increment"

    state = json.loads((tmp_path / "t" / "state.json").read_text(encoding="utf-8"))
    assert state["timesteps_done"] >= 800
    assert state["runs"] == 2


def test_both_paths_say_which_device_they_are_using(tmp_path: Path):
    """Asked as "is my graphics card being used?", and the answer was not there.

    Stable-Baselines3 prints "Using cuda device" when it constructs a model and
    nothing at all when it loads one — so on a resume, which is what most runs
    are, the line was missing. Its absence looked like bad news and meant
    nothing.
    """
    assert "device:   cpu" in run_trainer(tmp_path, 400), "the fresh path"
    assert "device:   cpu" in run_trainer(tmp_path, 800), "the resume path"
