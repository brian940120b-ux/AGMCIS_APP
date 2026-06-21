from pathlib import Path
from datetime import datetime
import shutil

backup_dir = Path("backups")
backup_dir.mkdir(exist_ok=True)

stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

files = [
    "data/paper_account.json",
    "data/paper_trades.json"
]

for f in files:
    p = Path(f)
    if p.exists():
        shutil.copy(
            p,
            backup_dir / f"{stamp}_{p.name}"
        )
        print(f"backup: {p}")
    else:
        print(f"missing: {p}")

print("backup complete")
