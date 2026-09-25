"""Training job tests (PHASE 18).

``train.py`` named the three things the dashboard had to be able to do before it
was allowed a START button — progress, cancellation, surviving a reload — so
those are what these test. The refusals matter as much as the successes: a job
that is queued silently behind another, or started on top of a running
simulation, would leave the operator watching a progress bar that means
something other than what it appears to.

The real-training tests use a tiny rollout so a cancellation lands in about a
second instead of after a full PPO block.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from core.config import load_settings
from training.jobs import FINISHED, MAX_METRIC_POINTS, TrainingJobRunner, TrainingRefusedError
from training.pipeline import TrainingUnavailableError, rl_available

requires_rl = pytest.mark.skipif(
    not rl_available(),
    reason="the RL stack is optional: pip install -r requirements-ml.txt",
)


@pytest.fixture
def runner(tmp_path):
    settings = load_settings()
    settings.training.output_directory = str(tmp_path / "models")
    # A short rollout so a cancellation is honoured within about a second,
    # rather than after a full 2048-step PPO block.
    settings.training.ppo.n_steps = 64
    settings.training.ppo.batch_size = 32
    return TrainingJobRunner(settings)


def _wait_for(runner: TrainingJobRunner, timeout: float = 240.0) -> None:
    deadline = time.time() + timeout
    while runner.busy() and time.time() < deadline:
        time.sleep(0.2)
    assert not runner.busy(), "the training job did not finish in time"


# ------------------------------------------------------------------ refusals


def test_a_job_is_refused_while_a_simulation_is_running(runner):
    """Both would fight for the same cores and neither's timings would mean anything."""
    runner.simulation_is_running = lambda: True
    with pytest.raises(TrainingRefusedError, match="simulation is running"):
        runner.start("ppo", timesteps=10)


def test_an_unknown_algorithm_is_refused(runner):
    with pytest.raises(TrainingRefusedError, match="algorithm must be one of"):
        runner.start("magic", timesteps=10)


def test_a_budget_above_the_cap_is_refused(runner):
    """A browser request must not be able to commit the server to a week of compute."""
    cap = runner.settings.training.max_timesteps_per_job
    with pytest.raises(TrainingRefusedError, match="above the"):
        runner.start("ppo", timesteps=cap + 1)


def test_zero_timesteps_is_refused(runner):
    with pytest.raises(TrainingRefusedError, match="at least 1"):
        runner.start("ppo", timesteps=0)


def test_a_job_is_refused_when_the_rl_stack_is_absent(runner, monkeypatch):
    monkeypatch.setattr("training.jobs.rl_available", lambda: False)
    with pytest.raises(TrainingUnavailableError, match=r"requirements-ml\.txt"):
        runner.start("ppo", timesteps=10)


def test_stopping_nothing_returns_nothing(runner):
    assert runner.stop() is None


def test_the_status_reports_the_cap_and_the_algorithms(runner):
    status = runner.status()
    assert status["busy"] is False
    assert status["current"] is None
    assert status["max_timesteps"] == runner.settings.training.max_timesteps_per_job
    assert "ppo" in status["algorithms"]
    assert "does not survive a restart" in status["notice"]


# ------------------------------------------------------------- real training


@requires_rl
def test_a_job_trains_reports_progress_and_finishes(runner):
    job = runner.start("ppo", timesteps=200, seed=11)
    assert job.state in {"PENDING", "RUNNING"}
    _wait_for(runner)

    finished = runner.get(job.job_id)
    assert finished is not None
    assert finished.state == "COMPLETED"
    assert finished.state in FINISHED
    # PPO collects in whole blocks, so it reaches *at least* what was asked for.
    assert finished.timesteps >= 200
    assert finished.metrics, "the job should have reported progress"
    assert finished.result is not None
    assert finished.error is None


@requires_rl
def test_a_second_job_is_refused_while_one_is_running(runner):
    from training.jobs import TrainingBusyError

    runner.start("ppo", timesteps=2000, seed=12)
    try:
        with pytest.raises(TrainingBusyError, match="already running"):
            runner.start("ppo", timesteps=200)
    finally:
        runner.stop()
        _wait_for(runner)


@requires_rl
def test_stopping_a_job_keeps_the_policy_it_trained(runner):
    """Cancelling is not discarding.

    ``learn`` is ended at a step boundary rather than the thread being killed,
    so the model is in a state worth saving. The run is recorded as CANCELLED
    rather than filed as a completed one that happens to be short.
    """
    from pathlib import Path

    job = runner.start("ppo", timesteps=100_000, seed=13)
    # Let it get past the first rollout so there is something to keep.
    deadline = time.time() + 60
    while runner.get(job.job_id).timesteps < 64 and time.time() < deadline:
        time.sleep(0.2)

    stopped = runner.stop()
    assert stopped is not None
    assert stopped.cancel_requested is True
    _wait_for(runner)

    final = runner.get(job.job_id)
    assert final.state == "CANCELLED"
    assert 0 < final.timesteps < final.requested_timesteps
    model_path = (final.result or {}).get("model_path")
    assert model_path and Path(model_path).exists(), "the policy trained so far must be saved"


@requires_rl
def test_the_progress_curve_stays_bounded(runner):
    """A long job must not keep a point per step in memory."""
    job = runner.start("ppo", timesteps=2000, seed=14)
    _wait_for(runner)
    final = runner.get(job.job_id)
    assert final.state == "COMPLETED"
    assert len(final.metrics) <= MAX_METRIC_POINTS


# --------------------------------------------------------------- bookkeeping


def test_an_interrupted_run_is_closed_out_rather_than_left_running(client):
    """A job cannot outlive the process running it, so RUNNING is stale on restart."""
    from core.runtime import get_run_manager
    from training.pipeline import TrainingPipeline

    repository = get_run_manager().repository
    assert repository is not None
    with repository.db.transaction() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO training_runs
                (training_id, algorithm, scenario_name, seed, config_hash,
                 started_at, status, total_steps, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("test-interrupted", "ppo", "training_navigation", 1, "x", time.time(), "RUNNING", 100, ""),
        )

    pipeline = TrainingPipeline(load_settings(), repository=repository)
    assert pipeline.reconcile_interrupted() >= 1

    row = repository.db.query_one(
        "SELECT status, notes FROM training_runs WHERE training_id = ?", ("test-interrupted",)
    )
    assert row["status"] == "INTERRUPTED"
    assert "server stopped" in row["notes"]


def test_recording_a_cancelled_run_says_so(client):
    from core.runtime import get_run_manager
    from training.pipeline import TrainingPipeline

    repository = get_run_manager().repository
    pipeline = TrainingPipeline(load_settings(), repository=repository)
    with repository.db.transaction() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO training_runs
                (training_id, algorithm, scenario_name, seed, config_hash,
                 started_at, status, total_steps, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("test-cancelled", "ppo", "training_navigation", 1, "x", time.time(), "RUNNING", 100, ""),
        )
    pipeline.record_status("test-cancelled", "CANCELLED", "stopped by the operator after 64 steps")

    row = repository.db.query_one(
        "SELECT status, notes FROM training_runs WHERE training_id = ?", ("test-cancelled",)
    )
    assert row["status"] == "CANCELLED"
    assert "64 steps" in row["notes"]


# ----------------------------------------------------------------------- API


def test_the_jobs_endpoint_reports_an_idle_server(client):
    body = client.get("/api/training/jobs").json()
    assert body["busy"] is False
    assert body["current"] is None
    assert body["max_timesteps"] > 0


def test_an_unknown_job_is_a_404(client):
    assert client.get("/api/training/jobs/JOB-nope").status_code == 404


def test_stopping_with_nothing_running_is_a_409(client):
    response = client.post("/api/training/stop")
    assert response.status_code == 409
    assert "no training job" in response.json()["detail"]


def test_starting_over_the_cap_is_a_400_that_says_so(client):
    response = client.post("/api/training/start", json={"timesteps": 10_000_000})
    assert response.status_code == 400
    assert "cap" in response.json()["detail"]


def test_starting_while_a_simulation_runs_is_refused_with_a_reason(client):
    client.post("/api/simulation/start", json={"scenario": "demo_alpha"})
    try:
        response = client.post("/api/training/start", json={"timesteps": 100})
        assert response.status_code == 400
        assert "simulation is running" in response.json()["detail"]
    finally:
        client.post("/api/simulation/stop")


def test_the_status_endpoint_no_longer_claims_training_is_cli_only(client):
    """PHASE 18 made the button real, so the API must stop saying it is not."""
    body = client.get("/api/training/status").json()
    assert body["browser_control"] is rl_available()
    assert body["max_timesteps_per_job"] > 0


# ------------------------------------------------- the progress bar is cosmetic


@pytest.fixture
def pipeline(tmp_path):
    from training.pipeline import TrainingPipeline

    settings = load_settings()
    settings.training.output_directory = str(tmp_path / "models")
    settings.training.ppo.n_steps = 64
    settings.training.ppo.batch_size = 32
    return TrainingPipeline(settings)


@requires_rl
def test_training_survives_a_missing_progress_bar(pipeline, monkeypatch, caplog):
    """`aifcs train` asked for a progress bar and died before its first step.

    SB3 raises ImportError out of `model.learn()` when tqdm and rich are absent,
    rather than drawing no bar. They are in requirements-ml.txt now, but losing a
    training run to a missing decoration is not a thing that should be possible.
    """
    monkeypatch.setattr("training.pipeline._progress_bar_available", lambda: False)

    with caplog.at_level("WARNING"):
        result = pipeline.train("ppo", total_timesteps=64, progress=True)

    assert result.total_timesteps >= 64
    assert Path(result.model_path).is_file()
    assert any("progress bar unavailable" in record.message for record in caplog.records)


@requires_rl
def test_the_progress_bar_is_used_when_it_can_be(pipeline, monkeypatch):
    """The guard must not have quietly turned the bar off for everyone."""
    from training.pipeline import _progress_bar_available

    if not _progress_bar_available():
        pytest.skip("tqdm and rich are not installed in this environment")

    seen = {}
    import stable_baselines3

    original = stable_baselines3.PPO.learn

    def spy(self, *args, **kwargs):
        seen["progress_bar"] = kwargs.get("progress_bar")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(stable_baselines3.PPO, "learn", spy)
    pipeline.train("ppo", total_timesteps=64, progress=True)

    assert seen["progress_bar"] is True
