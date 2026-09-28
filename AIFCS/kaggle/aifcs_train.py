"""Paste this into one Kaggle notebook cell, then Save Version -> Save & Run All.

Kaggle copies the notebook to its own machine and runs it there. The browser,
the laptop and the network stop mattering the moment you press the button, and
an email arrives when it finishes. That is the whole reason this file exists:
it is the only arrangement in this project where the work continues with the
computer switched off.

Before running, in the notebook's right-hand panel:

  * Settings -> Accelerator -> GPU P100   (or T4)
  * Settings -> Internet -> On            (needed to clone and pip install)
  * Input -> Add Input -> Datasets        (only from the second run on; see below)

What comes out lands in /kaggle/working and is downloadable from the finished
version's Output tab. To carry on from it next time, publish that output as a
Kaggle Dataset and attach it as an Input — the next run picks the checkpoints
up automatically.

MAX_HOURS is the important number. A free session is stopped at nine to twelve
hours, and whatever is running when that happens loses everything since its
last checkpoint. Finishing early on purpose is how the work survives.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = "https://github.com/brian940120b-ux/AGMCIS_APP.git"
BRANCH = "claude/aifcs-flight-simulation-u32h56"

#: What train.py returns when a run was stopped rather than finished, which is
#: what MAX_HOURS causes and is therefore the expected outcome here.
INTERRUPTED = 130

#: Stop with time to spare. Kaggle's own limit is 9-12 hours depending on the
#: accelerator; leaving an hour covers the evaluation that follows training.
MAX_HOURS = 7.5

#: One step of the ladder per session, because a session is not long enough for
#: three. The name is what to change between runs: v6, then v7, then v8.
NAME = "v7p"
POOL = ["v4", "v5"]
FLAGS = (
    "--reward pointed --rudder-limit 0.6 --ground-avoidance --g-limit 9 "
    "--action-repeat 6 --gamma 0.995 --gradient-steps -1 "
    "--observation extended --batch-size 512 --timesteps 2000000"
)

HOME = Path("/kaggle/working/AGMCIS_APP")
AIFCS = HOME / "AIFCS"
MODELS = AIFCS / "models" / "competition"


def run(command, **kwargs):
    print(f"\n$ {' '.join(command)}", flush=True)
    return subprocess.run(command, check=False, **kwargs).returncode


def setup() -> None:
    if not HOME.exists():
        run(["git", "clone", "--depth", "1", "--branch", BRANCH, REPO, str(HOME)])
    for requirements in ("requirements.txt", "requirements-ml.txt", "requirements-physics.txt"):
        run([sys.executable, "-m", "pip", "install", "-q", "-r", str(AIFCS / requirements)])


def bring_in_previous_sessions() -> None:
    """Copy checkpoints from any attached dataset into the models directory.

    Kaggle mounts datasets read-only under /kaggle/input, and training writes,
    so they are copied rather than linked. A first run has nothing attached and
    that is not an error — it only means the pool has to be empty.
    """
    MODELS.mkdir(parents=True, exist_ok=True)
    root = Path("/kaggle/input")
    if not root.is_dir():
        return
    for dataset in sorted(root.iterdir()):
        for session in sorted(dataset.glob("**/checkpoint.zip")):
            target = MODELS / session.parent.name
            if target.exists():
                continue
            shutil.copytree(session.parent, target)
            print(f"  brought in {session.parent.name}", flush=True)


def main() -> int:
    setup()
    bring_in_previous_sessions()

    available = [name for name in POOL if (MODELS / name / "checkpoint.zip").is_file()]
    missing = sorted(set(POOL) - set(available))
    if missing:
        # Refused, not warned. Last time this printed a line and carried on,
        # and seven GPU hours trained against the built-in drone instead of
        # the pool that was asked for — a different experiment, named the same
        # as the one on the laptop, and nobody noticed until the two were
        # scored side by side. A run that cannot be what it says it is should
        # not start.
        print(
            f"\n!! POOL asks for {', '.join(POOL)} and these are not here: {', '.join(missing)}", flush=True
        )
        print("   Attach them: Input -> Add Input -> your Dataset of sessions.", flush=True)
        print("   Or set POOL = [] above if training against the built-in", flush=True)
        print("   opponent is really what you want.", flush=True)
        print(f"\n!! 對手池少了 {', '.join(missing)},沒有開始訓練。", flush=True)
        return 2

    environment = dict(os.environ, PYTHONPATH=str(AIFCS / "backend"))
    train = [sys.executable, "-m", "competition.train", "--name", NAME]
    train += FLAGS.split()
    train += ["--max-hours", str(MAX_HOURS), "--device", "auto"]
    if available:
        train += ["--opponent-pool", *[str(MODELS / name / "checkpoint.zip") for name in available]]

    code = run(train, cwd=AIFCS, env=environment)

    # 130 is train.py's "somebody stopped this", and reaching MAX_HOURS is the
    # way this script stops itself. Treating it as a failure was the whole
    # arrangement working backwards: a session that used its full budget would
    # skip its evaluation and skip the copy into the Output tab, throwing away
    # the seven hours the budget existed to protect.
    if code == INTERRUPTED:
        print(f"\nstopped at the {MAX_HOURS} h budget with its checkpoint saved", flush=True)
    elif code != 0:
        print(f"\ntraining exited {code} — scoring is skipped, but whatever saved is kept", flush=True)

    if code in (0, INTERRUPTED):
        score = [sys.executable, "-m", "competition.evaluate", str(MODELS / NAME), "--baseline"]
        if "--ground-avoidance" in FLAGS.split():
            # Without this the baseline row is a centred stick with no floor,
            # which crashes every round and reports 0% as the bar a trained
            # policy has to clear. The real bar, with the floor, is 65%.
            score.append("--ground-avoidance")
        if available:
            score += ["--opponent-pool", *[str(MODELS / name / "checkpoint.zip") for name in available]]
        run(score, cwd=AIFCS, env=environment)

    # Out of the clone and into the notebook's own output, which is what the
    # Output tab offers for download and what a Dataset can be published from.
    # Unconditional: a run that failed at its last step still has hours of GPU
    # in its checkpoint, and Kaggle keeps nothing that is not in /kaggle/working.
    collected = 0
    for session in sorted(MODELS.iterdir()):
        if (session / "checkpoint.zip").is_file():
            shutil.copytree(session, Path("/kaggle/working") / session.name, dirs_exist_ok=True)
            collected += 1
    print(f"\n{collected} session(s) are in the Output tab", flush=True)
    return 0 if code in (0, INTERRUPTED) else code


if __name__ == "__main__":
    raise SystemExit(main())
