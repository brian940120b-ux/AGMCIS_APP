"""SQLite schema for AIFCS (PHASE 9).

One migration list, applied in order. Each entry is a version number and the
statements that take the database from the previous version to that one, so an
existing database is upgraded rather than rebuilt.

The replay file is the complete record of a run. What lives here is what you
want to *query*: which runs happened, how they ended, what was decided, what
they scored. Anything needing frame-by-frame detail goes back to the replay.
"""

from __future__ import annotations

# Tables created at version 1. Kept as one list so the whole shape of the
# database is readable in a single place.
_V1 = [
    """
    CREATE TABLE IF NOT EXISTS scenarios (
        name           TEXT PRIMARY KEY,
        description    TEXT NOT NULL DEFAULT '',
        entity_count   INTEGER NOT NULL DEFAULT 0,
        duration_s     REAL,
        seed           INTEGER,
        source_path    TEXT,
        first_seen_at  REAL NOT NULL,
        last_seen_at   REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS runs (
        run_id            TEXT PRIMARY KEY,
        scenario_name     TEXT NOT NULL,
        seed              INTEGER NOT NULL,
        config_hash       TEXT NOT NULL,
        integrator        TEXT NOT NULL DEFAULT '',
        tick_rate_hz      REAL NOT NULL,
        app_version       TEXT NOT NULL DEFAULT '',
        started_at        REAL NOT NULL,
        ended_at          REAL,
        end_reason        TEXT,
        ticks             INTEGER NOT NULL DEFAULT 0,
        simulation_time_s REAL NOT NULL DEFAULT 0.0,
        final_state_hash  TEXT,
        entity_count      INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY (scenario_name) REFERENCES scenarios (name) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_runs_started ON runs (started_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_runs_scenario ON runs (scenario_name)",
    """
    CREATE TABLE IF NOT EXISTS run_entities (
        run_id       TEXT NOT NULL,
        entity_id    TEXT NOT NULL,
        team         TEXT NOT NULL,
        agent_type   TEXT NOT NULL DEFAULT '',
        agent_id     TEXT,
        final_status TEXT,
        PRIMARY KEY (run_id, entity_id),
        FOREIGN KEY (run_id) REFERENCES runs (run_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id          TEXT NOT NULL,
        sequence        INTEGER NOT NULL,
        type            TEXT NOT NULL,
        simulation_time REAL NOT NULL,
        tick            INTEGER NOT NULL,
        entity_id       TEXT,
        agent_id        TEXT,
        message         TEXT NOT NULL DEFAULT '',
        data            TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY (run_id) REFERENCES runs (run_id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_events_run ON events (run_id, sequence)",
    "CREATE INDEX IF NOT EXISTS idx_events_type ON events (run_id, type)",
    """
    CREATE TABLE IF NOT EXISTS decisions (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id          TEXT NOT NULL,
        sequence        INTEGER NOT NULL,
        agent_id        TEXT NOT NULL,
        entity_id       TEXT NOT NULL,
        simulation_time REAL NOT NULL,
        tick            INTEGER NOT NULL,
        behaviour       TEXT NOT NULL DEFAULT '',
        confidence      REAL NOT NULL DEFAULT 0.0,
        reason_codes    TEXT NOT NULL DEFAULT '[]',
        action          TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY (run_id) REFERENCES runs (run_id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_decisions_run ON decisions (run_id, sequence)",
    "CREATE INDEX IF NOT EXISTS idx_decisions_agent ON decisions (run_id, agent_id)",
    """
    CREATE TABLE IF NOT EXISTS telemetry_samples (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id          TEXT NOT NULL,
        entity_id       TEXT NOT NULL,
        tick            INTEGER NOT NULL,
        simulation_time REAL NOT NULL,
        x               REAL NOT NULL,
        y               REAL NOT NULL,
        altitude_m      REAL NOT NULL,
        speed_mps       REAL NOT NULL,
        heading_deg     REAL NOT NULL,
        status          TEXT NOT NULL DEFAULT '',
        FOREIGN KEY (run_id) REFERENCES runs (run_id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_samples_run ON telemetry_samples (run_id, entity_id, tick)",
    """
    CREATE TABLE IF NOT EXISTS replays (
        run_id         TEXT PRIMARY KEY,
        path           TEXT NOT NULL,
        format         TEXT NOT NULL DEFAULT 'jsonl',
        compressed     INTEGER NOT NULL DEFAULT 0,
        record_rate_hz REAL NOT NULL,
        frames         INTEGER NOT NULL DEFAULT 0,
        bytes          INTEGER NOT NULL DEFAULT 0,
        created_at     REAL NOT NULL,
        FOREIGN KEY (run_id) REFERENCES runs (run_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS scores (
        run_id      TEXT NOT NULL,
        subject     TEXT NOT NULL,
        subject_id  TEXT NOT NULL,
        total       REAL NOT NULL,
        breakdown   TEXT NOT NULL DEFAULT '{}',
        weights_hash TEXT NOT NULL DEFAULT '',
        scored_at   REAL NOT NULL,
        PRIMARY KEY (run_id, subject, subject_id),
        FOREIGN KEY (run_id) REFERENCES runs (run_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS metrics (
        run_id     TEXT NOT NULL,
        entity_id  TEXT NOT NULL,
        name       TEXT NOT NULL,
        value      REAL NOT NULL,
        PRIMARY KEY (run_id, entity_id, name),
        FOREIGN KEY (run_id) REFERENCES runs (run_id) ON DELETE CASCADE
    )
    """,
    # Declared now so the schema is stable from PHASE 9 on. PHASE 11+ fills them.
    """
    CREATE TABLE IF NOT EXISTS training_runs (
        training_id  TEXT PRIMARY KEY,
        algorithm    TEXT NOT NULL,
        scenario_name TEXT,
        seed         INTEGER,
        config_hash  TEXT NOT NULL DEFAULT '',
        started_at   REAL NOT NULL,
        ended_at     REAL,
        status       TEXT NOT NULL DEFAULT 'PENDING',
        total_steps  INTEGER NOT NULL DEFAULT 0,
        notes        TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS models (
        model_id    TEXT PRIMARY KEY,
        training_id TEXT,
        algorithm   TEXT NOT NULL DEFAULT '',
        path        TEXT NOT NULL,
        created_at  REAL NOT NULL,
        notes       TEXT NOT NULL DEFAULT '',
        FOREIGN KEY (training_id) REFERENCES training_runs (training_id) ON DELETE SET NULL
    )
    """,
]

# version -> statements taking the database to that version.
MIGRATIONS: dict[int, list[str]] = {1: _V1}

LATEST_VERSION = max(MIGRATIONS)
