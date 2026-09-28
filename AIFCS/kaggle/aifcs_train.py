"""One rung of the ladder, run on Kaggle's GPU.

Not the thing to paste into the notebook — that is kaggle/bootstrap.py, which
clones the repository and runs this. Pasting this file works too, but then the
copy in the browser and the copy in git drift, and the version that actually
ran is the one nobody can read.

Kaggle copies the notebook to its own machine and runs it there. The browser,
the laptop and the network stop mattering the moment you press the button, and
an email arrives when it finishes. That is the whole reason this file exists:
it is the only arrangement in this project where the work continues with the
computer switched off.

Before running, in the notebook's right-hand panel:

  * Settings -> Accelerator -> GPU P100   (or T4)
  * Settings -> Internet -> On            (needed to clone and pip install)
  * Input -> Add Input -> the Dataset holding the opponents in POOL

The opponents are not optional. A POOL member that is not attached stops the
run before it starts: last time it printed a warning and carried on, and seven
GPU hours trained against the built-in drone under the name of the run that
was supposed to train against v4 and v5. Two different experiments, one name,
and nobody noticed until they were scored side by side.

Sessions are written straight to /kaggle/working/sessions, which is what the
Output tab shows. Straight there rather than copied at the end, so a
checkpoint is safe from the moment it is saved. The clone goes to /kaggle/temp
so the repository does not bury them.

To carry on from a run next time, publish its output as a Kaggle Dataset and
attach it as an Input — the next run picks those checkpoints up automatically.

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

#: The clone goes in scratch, not in the output. /kaggle/working is what the
#: Output tab shows and what a Dataset is published from, and a repository
#: dropped in there buries the thing you came for: last time the sessions were
#: in the output the whole while, under a card listing several hundred files
#: of trading system, and finding them took longer than the download.
HOME = Path("/kaggle/temp/AGMCIS_APP")
AIFCS = HOME / "AIFCS"

#: Sessions are written straight into the output rather than copied there at
#: the end, so a checkpoint is safe from the moment it is saved — a run killed
#: by the session limit at hour nine still leaves everything it had.
MODELS = Path("/kaggle/working/sessions")


def run(command, **kwargs):
    print(f"\n$ {' '.join(command)}", flush=True)
    return subprocess.run(command, check=False, **kwargs).returncode


def setup() -> None:
    HOME.parent.mkdir(parents=True, exist_ok=True)
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


def describe_inputs() -> str:
    """Everything mounted under /kaggle/input, and where the checkpoints are.

    A missing opponent has two causes that look identical from the outside:
    the Dataset was never attached, or it was attached and its folders are not
    named what the pool asks for. This prints enough to tell which without
    another round trip through Save & Run All.
    """
    root = Path("/kaggle/input")
    if not root.is_dir():
        return "   Nothing is attached: /kaggle/input does not exist.\n   完全沒有掛任何 Input。"

    lines = ["\n   What is attached under /kaggle/input:"]
    datasets = sorted(root.iterdir())
    if not datasets:
        lines.append("     (nothing)")
    for dataset in datasets:
        lines.append(f"     {dataset.name}/")
        found = sorted(dataset.glob("**/checkpoint.zip"))
        if not found:
            # Two levels is enough to see a flattened upload for what it is.
            for entry in sorted(dataset.iterdir())[:10]:
                lines.append(f"       {entry.name}{'/' if entry.is_dir() else ''}")
            lines.append("       ^ no checkpoint.zip anywhere in here")
        for checkpoint in found:
            # The folder name is what the pool matches on, so it is the thing
            # worth printing.
            lines.append(
                f"       {checkpoint.relative_to(dataset)}   -> would be session {checkpoint.parent.name!r}"
            )
    lines.append("   對手是用資料夾名字比對的,上面的名字要跟 POOL 一樣。")
    return "\n".join(lines)


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
        # What IS mounted, because "it is not there" and "it is there under a
        # name you did not expect" need different fixes and the message above
        # cannot tell them apart.
        print(describe_inputs(), flush=True)
        print("   Attach them: Input -> Add Input -> your Dataset of sessions.", flush=True)
        print("   Or set POOL = [] above if training against the built-in", flush=True)
        print("   opponent is really what you want.", flush=True)
        print(f"\n!! 對手池少了 {', '.join(missing)},沒有開始訓練。", flush=True)
        return 2

    environment = dict(os.environ, PYTHONPATH=str(AIFCS / "backend"))
    train = [sys.executable, "-m", "competition.train", "--name", NAME]
    train += FLAGS.split()
    train += ["--max-hours", str(MAX_HOURS), "--device", "auto", "--output", str(MODELS)]
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

    # Nothing to copy: MODELS is already inside /kaggle/working. Said out loud
    # anyway, because "where did it go" has cost more time on this than any
    # bug in it.
    print("\nin the Output tab, under sessions/:", flush=True)
    for session in sorted(MODELS.iterdir()):
        if (session / "checkpoint.zip").is_file():
            card = "with card.json" if (session / "card.json").is_file() else "NO card.json"
            print(f"  sessions/{session.name}  ({card})", flush=True)
    return 0 if code in (0, INTERRUPTED) else code


if __name__ == "__main__":
    raise SystemExit(main())
