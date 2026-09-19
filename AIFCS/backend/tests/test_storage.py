"""Storage layer tests (PHASE 9)."""

from __future__ import annotations

import pytest

from storage.database import Database
from storage.repository import RunRepository
from storage.schema import LATEST_VERSION


@pytest.fixture
def repo(tmp_path):
    return RunRepository(Database(tmp_path / "test.db"), max_decisions_per_run=5)


def test_migration_creates_every_table(tmp_path):
    db = Database(tmp_path / "a.db")
    assert db.version == LATEST_VERSION
    expected = {
        "scenarios",
        "runs",
        "run_entities",
        "events",
        "decisions",
        "telemetry_samples",
        "replays",
        "scores",
        "metrics",
        "training_runs",
        "models",
    }
    assert expected <= set(db.table_names())


def test_migration_is_idempotent(tmp_path):
    """Reopening a database must not rerun migrations or lose data."""
    path = tmp_path / "b.db"
    first = Database(path)
    RunRepository(first).upsert_scenario("demo_alpha")

    second = Database(path)
    assert second.migrate() == LATEST_VERSION
    assert len(RunRepository(second).list_scenarios()) == 1


def test_foreign_keys_are_enforced(tmp_path):
    """A row pointing at a run that does not exist must be refused."""
    import sqlite3

    db = Database(tmp_path / "c.db")
    with pytest.raises(sqlite3.IntegrityError), db.transaction() as conn:
        conn.execute(
            "INSERT INTO replays (run_id, path, record_rate_hz, created_at) VALUES (?,?,?,?)",
            ("ghost", "x.jsonl", 20.0, 0.0),
        )


def test_run_round_trip(repo):
    repo.create_run(
        "R1",
        scenario_name="demo_alpha",
        seed=7,
        config_hash="cfg",
        tick_rate_hz=60.0,
        entities=[{"entity_id": "BLUE-01", "team": "BLUE", "agent_type": "RuleAgent"}],
    )
    repo.finish_run(
        "R1",
        end_reason="duration reached",
        ticks=600,
        simulation_time_s=10.0,
        final_state_hash="hash",
        final_statuses={"BLUE-01": "ACTIVE"},
    )

    run = repo.get_run("R1")
    assert run is not None
    assert run["end_reason"] == "duration reached"
    assert run["final_state_hash"] == "hash"
    assert run["entities"][0]["final_status"] == "ACTIVE"


def test_decisions_respect_the_per_run_cap(repo):
    """The cap exists so decisions cannot fill the disk; the replay keeps them all."""
    repo.create_run("R1", scenario_name="s", seed=1, config_hash="c", tick_rate_hz=60.0)
    rows = [{"sequence": i, "agent_id": "A", "entity_id": "E"} for i in range(50)]

    assert repo.add_decisions("R1", rows) == 5
    assert repo.add_decisions("R1", rows) == 0
    assert repo.count_decisions("R1") == 5


def test_deleting_a_run_cascades(repo):
    repo.create_run("R1", scenario_name="s", seed=1, config_hash="c", tick_rate_hz=60.0)
    repo.add_events("R1", [{"sequence": 1, "type": "SIMULATION_STARTED"}])
    repo.add_decisions("R1", [{"sequence": 1, "agent_id": "A", "entity_id": "E"}])
    repo.add_telemetry_samples("R1", [{"entity_id": "E", "tick": 1}])
    repo.record_replay("R1", path="p", record_rate_hz=20.0, frames=1, size_bytes=1)
    repo.save_score("R1", subject="entity", subject_id="E", total=1.0, breakdown={})
    repo.save_metrics("R1", "E", {"x": 1.0})

    assert repo.delete_run("R1") is True
    assert repo.get_run("R1") is None
    assert repo.get_events("R1") == []
    assert repo.count_decisions("R1") == 0
    assert repo.get_telemetry_samples("R1") == []
    assert repo.get_replay("R1") is None
    assert repo.get_scores("R1") == []
    assert repo.get_metrics("R1") == {}


def test_unserialisable_event_data_does_not_raise(repo):
    """Event payloads come from anywhere; storage must never be the thing that breaks."""
    repo.create_run("R1", scenario_name="s", seed=1, config_hash="c", tick_rate_hz=60.0)
    repo.add_events("R1", [{"sequence": 1, "type": "X", "data": {"obj": object()}}])
    stored = repo.get_events("R1")
    assert len(stored) == 1


def test_list_runs_is_newest_first(repo):
    for index, run_id in enumerate(["R1", "R2", "R3"]):
        repo.create_run(
            run_id,
            scenario_name="s",
            seed=1,
            config_hash="c",
            tick_rate_hz=60.0,
            started_at=100.0 + index,
        )
    assert [r["run_id"] for r in repo.list_runs()] == ["R3", "R2", "R1"]
