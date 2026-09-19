"""Reading and writing AIFCS run history (PHASE 9).

The repository is the only thing that knows SQL. Callers hand it dictionaries
and get dictionaries back, so a schema change stays inside this file and
``storage/schema.py``.

Writes are batched inside one transaction wherever a caller has many rows: a
run can produce thousands of decisions, and committing each one separately is
what makes a database feel slow.
"""

from __future__ import annotations

import json
import time
from typing import Any

from core.logging_config import get_logger
from storage.database import Database

log = get_logger("storage.repository")


def _json(value: Any) -> str:
    """Serialise to JSON, never raising on an odd value inside event data."""
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return json.dumps({"unserialisable": str(value)})


def _loads(value: Any, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


class RunRepository:
    """Run history, decisions, events, replays, metrics and scores."""

    def __init__(self, db: Database, max_decisions_per_run: int = 5000) -> None:
        self.db = db
        self.max_decisions_per_run = max_decisions_per_run

    # ------------------------------------------------------------- scenarios

    def upsert_scenario(
        self,
        name: str,
        *,
        description: str = "",
        entity_count: int = 0,
        duration_s: float | None = None,
        seed: int | None = None,
        source_path: str | None = None,
    ) -> None:
        now = time.time()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO scenarios (name, description, entity_count, duration_s, seed,
                                       source_path, first_seen_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (name) DO UPDATE SET
                    description  = excluded.description,
                    entity_count = excluded.entity_count,
                    duration_s   = excluded.duration_s,
                    seed         = excluded.seed,
                    source_path  = excluded.source_path,
                    last_seen_at = excluded.last_seen_at
                """,
                (name, description, entity_count, duration_s, seed, source_path, now, now),
            )

    def list_scenarios(self) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM scenarios ORDER BY last_seen_at DESC")
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ runs

    def create_run(
        self,
        run_id: str,
        *,
        scenario_name: str,
        seed: int,
        config_hash: str,
        tick_rate_hz: float,
        integrator: str = "",
        app_version: str = "",
        started_at: float | None = None,
        entities: list[dict[str, Any]] | None = None,
    ) -> None:
        """Open a run. The scenario row is created first to satisfy the key."""
        self.upsert_scenario(scenario_name)
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO runs
                    (run_id, scenario_name, seed, config_hash, integrator, tick_rate_hz,
                     app_version, started_at, entity_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    scenario_name,
                    seed,
                    config_hash,
                    integrator,
                    tick_rate_hz,
                    app_version,
                    started_at if started_at is not None else time.time(),
                    len(entities or []),
                ),
            )
            for entity in entities or []:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO run_entities
                        (run_id, entity_id, team, agent_type, agent_id, final_status)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        entity["entity_id"],
                        entity.get("team", ""),
                        entity.get("agent_type", ""),
                        entity.get("agent_id"),
                        entity.get("final_status"),
                    ),
                )

    def finish_run(
        self,
        run_id: str,
        *,
        end_reason: str | None,
        ticks: int,
        simulation_time_s: float,
        final_state_hash: str | None = None,
        ended_at: float | None = None,
        final_statuses: dict[str, str] | None = None,
    ) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE runs
                   SET ended_at = ?, end_reason = ?, ticks = ?,
                       simulation_time_s = ?, final_state_hash = ?
                 WHERE run_id = ?
                """,
                (
                    ended_at if ended_at is not None else time.time(),
                    end_reason,
                    ticks,
                    simulation_time_s,
                    final_state_hash,
                    run_id,
                ),
            )
            for entity_id, status in (final_statuses or {}).items():
                conn.execute(
                    "UPDATE run_entities SET final_status = ? WHERE run_id = ? AND entity_id = ?",
                    (status, run_id, entity_id),
                )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM runs WHERE run_id = ?", (run_id,))
        if row is None:
            return None
        run = dict(row)
        run["entities"] = [
            dict(r)
            for r in self.db.query(
                "SELECT * FROM run_entities WHERE run_id = ? ORDER BY entity_id", (run_id,)
            )
        ]
        replay = self.db.query_one("SELECT * FROM replays WHERE run_id = ?", (run_id,))
        run["replay"] = dict(replay) if replay else None
        run["scores"] = self.get_scores(run_id)
        run["metrics"] = self.get_metrics(run_id)
        return run

    def list_runs(self, limit: int = 50, scenario_name: str | None = None) -> list[dict[str, Any]]:
        """Newest first, with the team scores folded in so one call fills a table."""
        sql = """
            SELECT r.*, rep.path AS replay_path, rep.frames AS replay_frames,
                   rep.bytes AS replay_bytes
              FROM runs r
              LEFT JOIN replays rep ON rep.run_id = r.run_id
        """
        params: tuple[Any, ...] = ()
        if scenario_name:
            sql += " WHERE r.scenario_name = ?"
            params = (scenario_name,)
        sql += " ORDER BY r.started_at DESC LIMIT ?"
        params = (*params, limit)

        runs = [dict(r) for r in self.db.query(sql, params)]
        for run in runs:
            run["scores"] = self.get_scores(run["run_id"])
        return runs

    def delete_run(self, run_id: str) -> bool:
        """Delete a run and everything hanging off it. Cascades do the rest."""
        with self.db.transaction() as conn:
            cursor = conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
        return cursor.rowcount > 0

    # ---------------------------------------------------------------- events

    def add_events(self, run_id: str, events: list[dict[str, Any]]) -> int:
        if not events:
            return 0
        rows = [
            (
                run_id,
                int(e.get("sequence", 0)),
                str(e.get("type", "")),
                float(e.get("simulation_time", 0.0)),
                int(e.get("tick", 0)),
                e.get("entity_id"),
                e.get("agent_id"),
                str(e.get("message", "")),
                _json(e.get("data", {})),
            )
            for e in events
        ]
        with self.db.transaction() as conn:
            conn.executemany(
                """
                INSERT INTO events
                    (run_id, sequence, type, simulation_time, tick, entity_id, agent_id, message, data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def get_events(
        self, run_id: str, limit: int = 500, event_type: str | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM events WHERE run_id = ?"
        params: tuple[Any, ...] = (run_id,)
        if event_type:
            sql += " AND type = ?"
            params = (*params, event_type)
        sql += " ORDER BY sequence LIMIT ?"
        rows = self.db.query(sql, (*params, limit))
        out = []
        for row in rows:
            event = dict(row)
            event["data"] = _loads(event.get("data"), {})
            out.append(event)
        return out

    # ------------------------------------------------------------- decisions

    def add_decisions(self, run_id: str, decisions: list[dict[str, Any]]) -> int:
        """Persist decisions, honouring the per-run cap.

        The cap exists because decisions are by far the highest-volume row type.
        The replay file always holds the complete stream, so what is dropped
        here is recoverable — which is why dropping is acceptable at all.
        """
        if not decisions or self.max_decisions_per_run == 0:
            return 0

        stored = self.count_decisions(run_id)
        room = self.max_decisions_per_run - stored
        if room <= 0:
            return 0

        batch = decisions[:room]
        rows = [
            (
                run_id,
                int(d.get("sequence", 0)),
                str(d.get("agent_id", "")),
                str(d.get("entity_id", "")),
                float(d.get("simulation_time", 0.0)),
                int(d.get("tick", 0)),
                str(d.get("behaviour", "")),
                float(d.get("confidence", 0.0)),
                _json(d.get("reason_codes", [])),
                _json(d.get("action", {})),
            )
            for d in batch
        ]
        with self.db.transaction() as conn:
            conn.executemany(
                """
                INSERT INTO decisions
                    (run_id, sequence, agent_id, entity_id, simulation_time, tick,
                     behaviour, confidence, reason_codes, action)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def count_decisions(self, run_id: str) -> int:
        row = self.db.query_one("SELECT COUNT(*) AS n FROM decisions WHERE run_id = ?", (run_id,))
        return int(row["n"]) if row else 0

    def get_decisions(
        self, run_id: str, limit: int = 500, agent_id: str | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM decisions WHERE run_id = ?"
        params: tuple[Any, ...] = (run_id,)
        if agent_id:
            sql += " AND agent_id = ?"
            params = (*params, agent_id)
        sql += " ORDER BY sequence LIMIT ?"
        out = []
        for row in self.db.query(sql, (*params, limit)):
            decision = dict(row)
            decision["reason_codes"] = _loads(decision.get("reason_codes"), [])
            decision["action"] = _loads(decision.get("action"), {})
            out.append(decision)
        return out

    # ------------------------------------------------------------- telemetry

    def add_telemetry_samples(self, run_id: str, samples: list[dict[str, Any]]) -> int:
        if not samples:
            return 0
        rows = [
            (
                run_id,
                str(s["entity_id"]),
                int(s.get("tick", 0)),
                float(s.get("simulation_time", 0.0)),
                float(s.get("x", 0.0)),
                float(s.get("y", 0.0)),
                float(s.get("altitude_m", 0.0)),
                float(s.get("speed_mps", 0.0)),
                float(s.get("heading_deg", 0.0)),
                str(s.get("status", "")),
            )
            for s in samples
        ]
        with self.db.transaction() as conn:
            conn.executemany(
                """
                INSERT INTO telemetry_samples
                    (run_id, entity_id, tick, simulation_time, x, y, altitude_m,
                     speed_mps, heading_deg, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def get_telemetry_samples(
        self, run_id: str, entity_id: str | None = None, limit: int = 5000
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM telemetry_samples WHERE run_id = ?"
        params: tuple[Any, ...] = (run_id,)
        if entity_id:
            sql += " AND entity_id = ?"
            params = (*params, entity_id)
        sql += " ORDER BY tick LIMIT ?"
        return [dict(r) for r in self.db.query(sql, (*params, limit))]

    # --------------------------------------------------------------- replays

    def record_replay(
        self,
        run_id: str,
        *,
        path: str,
        record_rate_hz: float,
        frames: int,
        size_bytes: int,
        compressed: bool = False,
        fmt: str = "jsonl",
    ) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO replays
                    (run_id, path, format, compressed, record_rate_hz, frames, bytes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, path, fmt, int(compressed), record_rate_hz, frames, size_bytes, time.time()),
            )

    def get_replay(self, run_id: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM replays WHERE run_id = ?", (run_id,))
        return dict(row) if row else None

    # ---------------------------------------------------------------- scores

    def save_score(
        self,
        run_id: str,
        *,
        subject: str,
        subject_id: str,
        total: float,
        breakdown: dict[str, Any],
        weights_hash: str = "",
    ) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO scores
                    (run_id, subject, subject_id, total, breakdown, weights_hash, scored_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, subject, subject_id, total, _json(breakdown), weights_hash, time.time()),
            )

    def get_scores(self, run_id: str) -> list[dict[str, Any]]:
        out = []
        for row in self.db.query(
            "SELECT * FROM scores WHERE run_id = ? ORDER BY subject, subject_id", (run_id,)
        ):
            score = dict(row)
            score["breakdown"] = _loads(score.get("breakdown"), {})
            out.append(score)
        return out

    # --------------------------------------------------------------- metrics

    def save_metrics(self, run_id: str, entity_id: str, metrics: dict[str, float]) -> int:
        rows = [(run_id, entity_id, name, float(value)) for name, value in metrics.items()]
        if not rows:
            return 0
        with self.db.transaction() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO metrics (run_id, entity_id, name, value) VALUES (?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def get_metrics(self, run_id: str) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for row in self.db.query(
            "SELECT entity_id, name, value FROM metrics WHERE run_id = ? ORDER BY entity_id, name",
            (run_id,),
        ):
            out.setdefault(str(row["entity_id"]), {})[str(row["name"])] = float(row["value"])
        return out
