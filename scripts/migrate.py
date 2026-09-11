"""
Migration runner。

用法:
    python scripts/migrate.py          # 套用尚未執行的 migration
    python scripts/migrate.py --status # 只顯示狀態,不做任何變更

每支 migration 只會被套用一次,紀錄在 schema_migrations 表。
全部 migration 都設計成 idempotent,重跑不會破壞資料。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db import get_connection  # noqa: E402

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def ensure_registry(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    VARCHAR(255) PRIMARY KEY,
            applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    """)


def applied_versions(cur):
    cur.execute("SELECT version FROM schema_migrations;")
    return {row[0] for row in cur.fetchall()}


def pending(cur):
    done = applied_versions(cur)
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    return [f for f in files if f.name not in done]


def main(status_only=False):
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                ensure_registry(cur)
                todo = pending(cur)

        if status_only:
            done = sorted(applied_versions(conn.cursor()))
            print(f"已套用 {len(done)} 支,待套用 {len(todo)} 支")
            for f in todo:
                print(f"  PENDING  {f.name}")
            return 0

        if not todo:
            print("沒有待套用的 migration")
            return 0

        for path in todo:
            sql = path.read_text(encoding="utf-8")
            with conn:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    cur.execute(
                        "INSERT INTO schema_migrations (version) VALUES (%s);",
                        (path.name,),
                    )
            print(f"  APPLIED  {path.name}")

        print(f"完成,共套用 {len(todo)} 支")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main(status_only="--status" in sys.argv))
