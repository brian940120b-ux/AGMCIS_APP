"""SQLite connection management and migrations (PHASE 9).

The standard library's ``sqlite3`` is enough here and adds no dependency. Two
details matter for correctness rather than taste:

* **A connection per thread.** FastAPI runs synchronous endpoints in a thread
  pool, and a SQLite connection may not be shared across threads. Each thread
  gets its own, created on first use.
* **WAL journalling.** Readers do not block the writer, so the dashboard can
  list past runs while a run in progress is still being written.

Foreign keys are enabled explicitly: SQLite leaves them off by default, and the
schema relies on ``ON DELETE CASCADE`` to make deleting a run clean up after
itself.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from core.logging_config import get_logger
from storage.schema import LATEST_VERSION, MIGRATIONS

log = get_logger("storage")


class Database:
    """A migrated SQLite database, safe to share across threads."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if self.path.parent != Path(""):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        # Migrations run once per process, not once per thread.
        self._migration_lock = threading.Lock()
        self._migrated = False
        self.migrate()

    # ----------------------------------------------------------- connections

    @property
    def connection(self) -> sqlite3.Connection:
        """This thread's connection, opened on first use."""
        existing: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if existing is not None:
            return existing

        conn = sqlite3.connect(self.path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        # WAL is a property of the database file, not the connection, but
        # setting it is idempotent and costs nothing.
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        self._local.conn = conn
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run a block in one transaction, rolling back if it raises."""
        conn = self.connection
        try:
            with conn:
                yield conn
        except sqlite3.Error:
            log.exception("database transaction failed", extra={"event": "DB_TRANSACTION_FAILED"})
            raise

    def close(self) -> None:
        """Close this thread's connection, if it has one."""
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # ------------------------------------------------------------ migrations

    @property
    def version(self) -> int:
        row = self.connection.execute("PRAGMA user_version").fetchone()
        return int(row[0]) if row else 0

    def migrate(self) -> int:
        """Apply any migrations this database has not seen. Returns the version."""
        with self._migration_lock:
            conn = self.connection
            current = self.version
            if current >= LATEST_VERSION:
                self._migrated = True
                return current

            for version in sorted(MIGRATIONS):
                if version <= current:
                    continue
                with conn:
                    for statement in MIGRATIONS[version]:
                        conn.execute(statement)
                    # PRAGMA does not accept a bound parameter.
                    conn.execute(f"PRAGMA user_version = {int(version)}")
                log.info(
                    "database migrated",
                    extra={"event": "DB_MIGRATED", "from": current, "to": version, "path": str(self.path)},
                )
                current = version

            self._migrated = True
            return current

    # --------------------------------------------------------------- helpers

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        return self.connection.execute(sql, params)

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        return list(self.connection.execute(sql, params).fetchall())

    def query_one(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        return self.connection.execute(sql, params).fetchone()

    def table_names(self) -> list[str]:
        rows = self.query("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
        return [str(r["name"]) for r in rows if not str(r["name"]).startswith("sqlite_")]

    def stats(self) -> dict[str, Any]:
        """Row counts per table plus the file size, for the status panel."""
        counts: dict[str, int] = {}
        for table in self.table_names():
            row = self.query_one(f"SELECT COUNT(*) AS n FROM {table}")
            counts[table] = int(row["n"]) if row else 0
        return {
            "path": str(self.path),
            "exists": self.path.is_file(),
            "size_bytes": self.path.stat().st_size if self.path.is_file() else 0,
            "schema_version": self.version,
            "rows": counts,
        }
