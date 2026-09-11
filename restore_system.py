"""
還原。

與 backup_system.py 配對:備份改成 pg_dump 之後,還原也必須從 SQL dump 還原。
原本還原的是 data/paper_*.json,而那些檔案自 V13.4 之後就沒有人寫入了。

用法:
    python restore_system.py                 # 列出可用備份
    python restore_system.py <檔名>          # 還原指定備份
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import DB_CONFIG  # noqa: E402

BACKUP_DIR = Path(os.getenv("BACKUP_DIR", "backups"))


def list_backups():
    dumps = sorted(BACKUP_DIR.glob("*.sql.gz"))
    if not dumps:
        print(f"{BACKUP_DIR} 內沒有任何 .sql.gz 備份")
        return dumps

    print("可用備份:")
    for dump in dumps:
        print(f"  {dump.name}  ({dump.stat().st_size / 1024:.1f} KB)")
    return dumps


def restore(name):
    target = BACKUP_DIR / name
    if not target.exists():
        print(f"找不到備份:{target}")
        return 1

    if not DB_CONFIG["password"]:
        print("FAILED: DB_PASSWORD 未設定")
        return 1

    # 還原會覆蓋現有資料,要求明確確認。
    print(f"即將把 {target.name} 還原到資料庫 {DB_CONFIG['dbname']}@{DB_CONFIG['host']}")
    print("這會覆蓋現有的交易與帳戶資料。")
    if input("輸入 RESTORE 確認:").strip() != "RESTORE":
        print("已取消")
        return 1

    env = dict(os.environ, PGPASSWORD=DB_CONFIG["password"])
    command = (
        f"gunzip -c {target} | psql -h {DB_CONFIG['host']} -p {DB_CONFIG['port']} "
        f"-U {DB_CONFIG['user']} -d {DB_CONFIG['dbname']}"
    )

    result = subprocess.run(command, shell=True, env=env, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"FAILED: {result.stderr.strip()}")
        return 1

    print(f"restored: {target.name}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        list_backups()
        print("\n用法: python restore_system.py <備份檔名>")
        sys.exit(1)
    sys.exit(restore(sys.argv[1]))
