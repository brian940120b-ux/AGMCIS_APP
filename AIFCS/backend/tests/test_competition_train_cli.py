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


def run_trainer(output: Path, timesteps: int, checkpoint_every: int = 1_000_000) -> str:
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
            "--checkpoint-every",
            str(checkpoint_every),
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


def test_a_long_run_says_how_it_is_going_while_it_goes(tmp_path: Path):
    """Asked as "where do I find the progress?", and the answer was nowhere.

    Between the banner and the final summary the trainer printed nothing of its
    own — on a run of several hours, that is a window full of silence, which is
    what a hang looks like too. Each checkpoint now reports the figure it has
    just written to disk.
    """
    output = run_trainer(tmp_path, 400, checkpoint_every=100)

    progress = [line for line in output.splitlines() if "steps (" in line and "%" in line]
    assert len(progress) >= 2, f"expected progress during the run, got:\n{output}"
    # And it says how much longer, which is the question behind the question.
    assert any("left" in line for line in progress), progress


def test_a_resumed_session_flies_the_floor_it_recorded_end_to_end():
    """The whole path, not just the helper.

    A first version of this tested `build_floor` on its own. That test passed
    with the call site reverted to `GroundAvoidance()`, which is the mutation
    that matters — a correct helper nothing routes through fixes nothing.
    Go through `parse_args` and `inherit` the way a resume does, and assert on
    the EnvConfig the run is actually given.
    """
    from competition.session import SessionState
    from competition.train import apply_defaults, build_config, inherit, parse_args

    args = parse_args(["--name", "v2"])
    state = SessionState(
        name="v2",
        algorithm="sac",
        target_timesteps=2_000_000,
        environment={
            "ground_avoidance": {
                "seconds_to_impact": 8.0,
                "ceiling_ft": 8000.0,
                "floor_ft": 500.0,
                "elevator": 0.6,
                "roll_gain": 0.02,
            }
        },
    )
    inherit(args, state)  # the order train() uses
    apply_defaults(args)
    floor = build_config(args).ground_avoidance

    assert floor is not None, "the session had one; the resume must keep it"
    assert floor.elevator == 0.6, "today's default is 1.0 — that would be a new aircraft"
    assert floor.seconds_to_impact == 8.0
    assert floor.ceiling_ft == 8000.0


def test_a_resume_rebuilds_the_floor_it_recorded_not_todays_default():
    """The defaults moved. A session that started before that must not change
    aircraft on its next resume.

    The old converter turned the recorded settings into a bare `True`, and the
    environment was then built with `GroundAvoidance()` — whatever the defaults
    happen to be today. Every pre-2026-09-26 session would have silently
    switched from 0.6/8s/8k to 1.0/12s/15k halfway through training.
    """
    from competition.train import build_floor

    recorded = {
        "seconds_to_impact": 8.0,
        "ceiling_ft": 8000.0,
        "floor_ft": 500.0,
        "elevator": 0.6,
        "roll_gain": 0.02,
    }
    rebuilt = build_floor(recorded)
    assert rebuilt is not None
    assert rebuilt.elevator == 0.6
    assert rebuilt.seconds_to_impact == 8.0
    assert rebuilt.ceiling_ft == 8000.0


def test_the_flag_on_a_fresh_run_still_means_todays_defaults():
    from competition.safety import GroundAvoidance
    from competition.train import build_floor

    assert build_floor(True) == GroundAvoidance()
    assert build_floor(False) is None
    assert build_floor(None) is None


def test_the_recorded_floor_survives_inheritance_as_settings_not_a_flag():
    """The converter is the half of this that runs first: it decides what the
    resumed run is handed. A bool there loses the settings before the
    environment is ever built."""
    from competition.train import CONVERTERS

    recorded = {"elevator": 0.6, "ceiling_ft": 8000.0}
    assert CONVERTERS["ground_avoidance"](recorded) == recorded
    assert CONVERTERS["ground_avoidance"](None) is None
