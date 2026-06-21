from pathlib import Path
import shutil
import sys

backup_dir = Path("backups")

if len(sys.argv) < 2:
    print("Usage: python restore_system.py <backup_prefix>")
    sys.exit(1)

prefix = sys.argv[1]

targets = [
    ("paper_account.json", "data/paper_account.json"),
    ("paper_trades.json", "data/paper_trades.json"),
]

for backup_name, restore_path in targets:

    matches = list(
        backup_dir.glob(f"{prefix}_{backup_name}")
    )

    if matches:
        shutil.copy(matches[0], restore_path)
        print("restored:", restore_path)
    else:
        print("missing:", backup_name)

print("restore complete")
