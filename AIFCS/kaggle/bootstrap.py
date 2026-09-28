"""Paste this into one Kaggle cell. It never needs changing again.

Everything that varies between runs — which rung, which opponents, which
flags — lives in kaggle/aifcs_train.py in the repository, so changing a run
means a commit and a rerun of this same cell, not another 130 lines pasted
into a browser at one in the morning.

Before pressing Save Version -> Save & Run All, in the right-hand panel:

  * Settings -> Accelerator -> GPU T4 x2  (or P100)
  * Settings -> Internet    -> On
  * Input    -> Add Input   -> the Dataset holding v4 and v5

The run stops and says so if the opponents are not attached. That is
deliberate: a session that cannot be the experiment it is named after should
not spend seven GPU hours pretending to be.
"""

import subprocess
import sys
from pathlib import Path

REPO = "https://github.com/brian940120b-ux/AGMCIS_APP.git"
BRANCH = "claude/aifcs-flight-simulation-u32h56"
HOME = Path("/kaggle/working/AGMCIS_APP")

if not HOME.exists():
    subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", BRANCH, REPO, str(HOME)],
        check=True,
    )
else:
    # A rerun in the same session: take whatever was pushed since.
    subprocess.run(["git", "-C", str(HOME), "fetch", "--depth", "1", "origin", BRANCH], check=False)
    subprocess.run(["git", "-C", str(HOME), "reset", "--hard", f"origin/{BRANCH}"], check=False)

trainer = HOME / "AIFCS" / "kaggle" / "aifcs_train.py"
print(f"running {trainer}", flush=True)
print(
    subprocess.run(
        ["git", "-C", str(HOME), "log", "--oneline", "-1"], capture_output=True, text=True, encoding="utf-8"
    ).stdout,
    flush=True,
)

code = subprocess.call([sys.executable, str(trainer)])
if code:
    # Fail the cell, so the finished version is marked failed rather than
    # looking like a run that produced nothing on purpose.
    raise SystemExit(code)
print("\ndone - see the Output tab, under sessions/")
