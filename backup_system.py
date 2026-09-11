"""
備份。

Phase 0.5 的修正:原本備份 data/paper_account.json 與 data/paper_trades.json,
但 V13.4 之後交易狀態已全部搬到 PostgreSQL(paper_trading.save_trades() 是空函式),
backups/ 裡的 JSON 全是過期資料 —— 等於實際上沒有任何交易狀態的備份,
而使用者以為有。

現在改用 pg_dump 備份真正存放交易狀態的資料庫。
"""
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import db_config  # noqa: E402

CONFIG = db_config()
BACKUP_DIR = Path(os.getenv("BACKUP_DIR", "backups"))
KEEP_LAST = int(os.getenv("BACKUP_KEEP_LAST", "30"))


def run_backup():
    BACKUP_DIR.mkdir(exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = BACKUP_DIR / f"{stamp}_{CONFIG['dbname']}.sql.gz"

    if not CONFIG["password"]:
        print("FAILED: DB_PASSWORD 未設定,無法備份資料庫")
        return 1

    env = dict(os.environ, PGPASSWORD=CONFIG["password"])

    command = (
        f"pg_dump -h {CONFIG['host']} -p {CONFIG['port']} "
        f"-U {CONFIG['user']} -d {CONFIG['dbname']} | gzip > {target}"
    )

    result = subprocess.run(command, shell=True, env=env,
                            capture_output=True, text=True)

    if result.returncode != 0 or not target.exists() or target.stat().st_size == 0:
        print(f"FAILED: pg_dump 失敗 -> {result.stderr.strip()}")
        if target.exists():
            target.unlink()
        return 1

    size_kb = target.stat().st_size / 1024
    print(f"backup: {target} ({size_kb:.1f} KB)")

    prune()
    return 0


def prune():
    """只保留最近 KEEP_LAST 份,避免備份目錄無限長大。"""
    dumps = sorted(BACKUP_DIR.glob("*.sql.gz"))
    for old in dumps[:-KEEP_LAST]:
        old.unlink()
        print(f"pruned: {old}")


if __name__ == "__main__":
    sys.exit(run_backup())
