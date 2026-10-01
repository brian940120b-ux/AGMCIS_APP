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
  * Input -> Add Input -> the Dataset holding the experiment's opponent pool

The opponents are not optional. A pool member that is not attached stops the
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

#: Which experiment this session runs. The record under experiments/ holds the
#: session name, the flags and the opponent pool, so the thing that runs is the
#: thing that was declared. Two ways to choose it:
#:
#:   * the notebook cell sets AIFCS_EXPERIMENT before bootstrap.py runs, which
#:     is how three experiments become three Kaggle versions without three
#:     commits (and without a laptop that can reach GitHub);
#:   * otherwise this default, which is the next experiment in the queue.
#:
#: Printed at the top of the log either way, so a run says what it is.
EXPERIMENT = os.environ.get("AIFCS_EXPERIMENT", "EXP-008-doctrine-pool").strip()

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


#: Where the repository itself is left for download, beside sessions/.
BUNDLE = Path("/kaggle/working/AGMCIS_APP.bundle")


def export_bundle(home: Path = HOME, target: Path = BUNDLE) -> bool:
    """Leave the repository in the Output tab as a git bundle.

    The laptop cannot reach github.com (two days of "Connection was reset")
    and Kaggle can, so Kaggle becomes the courier: a bundle is a single file
    git can pull from exactly as it would from the remote, history and all:

        git pull /c/Users/user/Downloads/AGMCIS_APP.bundle claude/aifcs-flight-simulation-u32h56

    The clone above is shallow and git will not bundle a shallow repository,
    so the history is fetched first. Never fatal: a run that cannot make the
    bundle still trains.
    """
    try:
        run(["git", "fetch", "--unshallow", "--quiet"], cwd=home)
        code = run(["git", "bundle", "create", str(target), "--all"], cwd=home)
    except OSError as error:
        print(f"bundle skipped: {error}", flush=True)
        return False
    if code != 0 or not target.is_file():
        print("bundle skipped: git could not create it (training carries on)", flush=True)
        return False
    print(
        f"repository bundle for the laptop: {target.name} ({target.stat().st_size / 1e6:.1f} MB)", flush=True
    )
    return True


#: What an extracted Stable-Baselines3 model looks like from the outside, used
#: to recognise one Kaggle has unpacked rather than trusting a folder's name.
SB3_MARKER = "policy.pth"


def _session_folders(dataset: Path) -> dict[str, Path]:
    """Every session in an attached dataset, however Kaggle left it.

    Three shapes, because putting a model on Kaggle is not as simple as it
    sounds:

    * ``v4/checkpoint.zip`` — what was uploaded, if it survived.
    * ``v4/checkpoint/`` — what usually arrives instead. Kaggle unpacks .zip
      files when a Dataset is created, and a Stable-Baselines3 model *is* a
      zip, so checkpoint.zip reaches the notebook as a directory and the file
      the pool looks for no longer exists. Nothing warns you: the only sign is
      the Dataset preview reading "1 directories, 1 files".
    * ``v4/checkpoint.sb3`` — the same file renamed before upload to dodge
      that, which is the tidier fix when building a Dataset fresh.

    Returned as {session name: the folder holding it}, so the caller can match
    on the name the pool asks for.
    """
    found: dict[str, Path] = {}
    for entry in sorted(dataset.glob("**/*")):
        packed = entry.is_file() and entry.name in ("checkpoint.zip", "checkpoint.sb3")
        unpacked = entry.is_dir() and (entry / SB3_MARKER).is_file()
        if packed or unpacked:
            # Either way the session is the folder *containing* it, and that
            # folder's name is what the pool matches on.
            found.setdefault(entry.parent.name, entry.parent)
    return found


def _install(source: Path, target: Path) -> None:
    """Put one session where training can read and write it.

    Kaggle mounts /kaggle/input read-only and training writes, so this copies
    rather than links, and rebuilds the checkpoint if it arrived unpacked.
    Re-zipping is exact: the archive that comes out has the same entries with
    the same bytes as the one that went in.
    """
    target.mkdir(parents=True, exist_ok=True)
    for entry in sorted(source.iterdir()):
        if entry.is_file() and entry.name == "checkpoint.sb3":
            shutil.copy2(entry, target / "checkpoint.zip")
        elif entry.is_file():
            shutil.copy2(entry, target / entry.name)
        elif entry.is_dir() and (entry / SB3_MARKER).is_file():
            shutil.make_archive(str(target / "checkpoint"), "zip", root_dir=entry)
            print(f"    rebuilt checkpoint.zip from {entry.name}/ (Kaggle unpacked it)", flush=True)


def bring_in_previous_sessions() -> None:
    """Copy checkpoints from any attached dataset into the models directory.

    A first run has nothing attached and that is not an error — it only means
    the pool has to be empty.
    """
    MODELS.mkdir(parents=True, exist_ok=True)
    root = Path("/kaggle/input")
    if not root.is_dir():
        return
    for dataset in sorted(root.iterdir()):
        for name, folder in _session_folders(dataset).items():
            target = MODELS / name
            if target.exists():
                continue
            _install(folder, target)
            card = "with card.json" if (target / "card.json").is_file() else "NO card.json"
            print(f"  brought in {name} ({card})", flush=True)


def describe_inputs() -> str:
    """Everything mounted under /kaggle/input, and where the checkpoints are.

    A missing opponent has causes that look identical from the browser: the
    Dataset was never attached, or it was attached and its folders are not
    named what the pool asks for, or Kaggle unpacked the checkpoints on the way
    in. This prints enough to tell which without another round trip through
    Save & Run All.
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
        found = _session_folders(dataset)
        for name, folder in found.items():
            lines.append(f"       {folder.relative_to(dataset)}/   -> session {name!r}")
        if not found:
            # Two levels is enough to see a flattened upload for what it is.
            for entry in sorted(dataset.iterdir())[:10]:
                lines.append(f"       {entry.name}{'/' if entry.is_dir() else ''}")
            lines.append("       ^ no checkpoint here, unpacked or otherwise")
    lines.append("   對手是用資料夾名字比對的,上面的名字要跟實驗紀錄裡的 pool 一樣。")
    return "\n".join(lines)


def load_experiment() -> tuple[str, list[str], list[str]]:
    """(session name, train flags, opponent pool) from the declared record.

    Imported from the clone rather than duplicated here, so the record's
    format has one owner. Marked RUNNING on the way past, which is the only
    write this script makes to the record: the result is written on the laptop,
    from the scoreboard, by a person.
    """
    sys.path.insert(0, str(AIFCS / "backend"))
    from competition.experiments import Experiment, mark_running

    record: Experiment = mark_running(EXPERIMENT)
    print(f"experiment {record.id}: {record.question}", flush=True)
    print(f"  session {record.session}  reward tier {record.reward_tier}", flush=True)
    return record.session, record.train_flags(), list(record.pool)


def split_pool(pool: list[str]) -> tuple[list[str], list[str]]:
    """(scripted names, session names). Only the second kind has to be on disk.

    A pool may name scripted opponents ("break", "scissors", ...) beside saved
    sessions; the names ride through to train.py as themselves and the
    environment builds them each round. Anything that is not such a name is a
    session folder the Dataset has to hold.
    """
    from competition.environment import opponent_names

    names = set(opponent_names())
    return [n for n in pool if n in names], [n for n in pool if n not in names]


def main() -> int:
    setup()
    export_bundle()
    name, flags, pool = load_experiment()
    bring_in_previous_sessions()

    scripted, sessions = split_pool(pool)
    available = [n for n in sessions if (MODELS / n / "checkpoint.zip").is_file()]
    missing = sorted(set(sessions) - set(available))
    if missing:
        # Refused, not warned. Last time this printed a line and carried on,
        # and seven GPU hours trained against the built-in drone instead of
        # the pool that was asked for — a different experiment, named the same
        # as the one on the laptop, and nobody noticed until the two were
        # scored side by side. A run that cannot be what it says it is should
        # not start.
        print(
            f"\n!! the experiment's pool asks for {', '.join(pool)} and these are not here: "
            f"{', '.join(missing)}",
            flush=True,
        )
        # What IS mounted, because "it is not there" and "it is there under a
        # name you did not expect" need different fixes and the message above
        # cannot tell them apart.
        print(describe_inputs(), flush=True)
        print("   Attach them: Input -> Add Input -> your Dataset of sessions.", flush=True)
        print(f"   Or empty the pool in experiments/{EXPERIMENT}.yaml if training", flush=True)
        print("   opponent is really what you want.", flush=True)
        print(f"\n!! 對手池少了 {', '.join(missing)},沒有開始訓練。", flush=True)
        return 2

    environment = dict(os.environ, PYTHONPATH=str(AIFCS / "backend"))
    train = [sys.executable, "-m", "competition.train", "--name", name]
    train += flags
    train += ["--max-hours", str(MAX_HOURS), "--device", "auto", "--output", str(MODELS)]
    if scripted or available:
        train += ["--opponent-pool", *scripted]
        train += [str(MODELS / name / "checkpoint.zip") for name in available]

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
        score = [sys.executable, "-m", "competition.evaluate", str(MODELS / name), "--baseline"]
        if "--ground-avoidance" in flags:
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
    if BUNDLE.is_file():
        print(
            f"\nin the Output tab: {BUNDLE.name} — the repository, for a laptop that cannot reach GitHub",
            flush=True,
        )
    print("\nin the Output tab, under sessions/:", flush=True)
    for session in sorted(MODELS.iterdir()):
        if (session / "checkpoint.zip").is_file():
            card = "with card.json" if (session / "card.json").is_file() else "NO card.json"
            print(f"  sessions/{session.name}  ({card})", flush=True)
    return 0 if code in (0, INTERRUPTED) else code


if __name__ == "__main__":
    raise SystemExit(main())
