"""Paste this into one Kaggle cell. It never needs changing again.

Everything that varies between runs — which rung, which opponents, which
flags — lives in kaggle/aifcs_train.py in the repository, so changing a run
means a commit and a rerun of this same cell, not another 130 lines pasted
into a browser at one in the morning.

To choose the experiment, put one line above this block in the same cell:

    import os; os.environ["AIFCS_EXPERIMENT"] = "EXP-008-doctrine-pool"

Leave it out and aifcs_train.py runs its default. One version per
experiment; they can run side by side as far as Kaggle's GPU quota allows.

Before pressing Save Version -> Save & Run All, in the right-hand panel:

  * Settings -> Accelerator -> GPU T4 x2  (or P100)
  * Settings -> Internet    -> On
  * Input    -> Add Input   -> the Dataset holding v4 and v5

The run stops and says so if the opponents are not attached. That is
deliberate: a session that cannot be the experiment it is named after should
not spend seven GPU hours pretending to be.
"""

import shutil
import subprocess
import sys
from pathlib import Path

REPO = "https://github.com/brian940120b-ux/AGMCIS_APP.git"
BRANCH = "claude/aifcs-flight-simulation-u32h56"

#: Scratch, not /kaggle/working. Two reasons, both learned the hard way.
#: /kaggle/working is what the Output tab lists, and a repository dropped in
#: there buries the sessions you came for. And it is *restored from the
#: previous version's output* when a new version starts — so a clone left
#: there comes back stale, and the first version of this tried to update it in
#: place with a fetch and a reset that were allowed to fail quietly. They did.
#: A run then used week-old code while its log said nothing was wrong, and the
#: only clue was a diagnostic that should have printed and did not.
HOME = Path("/kaggle/temp/AGMCIS_APP")

# Deleted and recloned, every time. A clone takes seconds; being unsure which
# code ran costs a six-hour round trip.
for stale in (HOME, Path("/kaggle/working/AGMCIS_APP")):
    if stale.exists():
        print(f"removing stale {stale}", flush=True)
        shutil.rmtree(stale, ignore_errors=True)

HOME.parent.mkdir(parents=True, exist_ok=True)
subprocess.run(
    ["git", "clone", "--depth", "1", "--branch", BRANCH, REPO, str(HOME)],
    check=True,
)

# Printed loudly, at the top, because "which version of the code ran" was the
# question that could not be answered from the log.
head = subprocess.run(
    ["git", "-C", str(HOME), "log", "--oneline", "-1"],
    capture_output=True,
    text=True,
    encoding="utf-8",
).stdout.strip()
print(f"\n=== running commit {head} ===\n", flush=True)

code = subprocess.call([sys.executable, str(HOME / "AIFCS" / "kaggle" / "aifcs_train.py")])
if code:
    # Fail the cell, so the finished version is marked failed rather than
    # looking like a run that produced nothing on purpose.
    raise SystemExit(code)
print("\ndone - see the Output tab, under sessions/")
