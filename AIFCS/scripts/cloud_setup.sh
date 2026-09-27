#!/usr/bin/env bash
# Set up a rented Linux GPU box to train. Run once, on a fresh machine.
#
#   curl -fsSL https://raw.githubusercontent.com/brian940120b-ux/AGMCIS_APP/claude/aifcs-flight-simulation-u32h56/AIFCS/scripts/cloud_setup.sh | bash
#
# or, if the repo is already cloned:  bash AIFCS/scripts/cloud_setup.sh
#
# Installs nothing the machine already has, and says what it found rather than
# assuming. The competition package and the organiser's documents are NOT
# needed and are NOT fetched: training uses the JSBSim F-16 that ships with the
# pip package, and that was checked against the real host — ten seconds into an
# identical dive the two aircraft were three feet apart.
set -euo pipefail

REPO="${AIFCS_REPO:-https://github.com/brian940120b-ux/AGMCIS_APP.git}"
BRANCH="${AIFCS_BRANCH:-claude/aifcs-flight-simulation-u32h56}"
HOME_DIR="${AIFCS_HOME:-$HOME/AGMCIS_APP}"

say() { printf '\n==> %s\n' "$1"; }

say "checking the machine"
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
else
  echo "    no nvidia-smi: this box has no GPU, or no driver."
  echo "    Training will still run on the CPU, far more slowly."
fi
echo "    cores: $(nproc)"
echo "    free:  $(free -h 2>/dev/null | awk '/^Mem:/ {print $7}' || echo unknown)"
echo "    disk:  $(df -h . | awk 'NR==2 {print $4}') available"

say "system packages"
if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq git python3-venv python3-dev build-essential
fi

say "the repository"
if [ -d "$HOME_DIR/.git" ]; then
  git -C "$HOME_DIR" fetch origin "$BRANCH"
  git -C "$HOME_DIR" checkout "$BRANCH"
  git -C "$HOME_DIR" pull --ff-only origin "$BRANCH"
else
  git clone --branch "$BRANCH" "$REPO" "$HOME_DIR"
fi

cd "$HOME_DIR/AIFCS"

say "python environment"
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -r requirements.txt
./.venv/bin/pip install --quiet -r requirements-ml.txt
./.venv/bin/pip install --quiet -r requirements-physics.txt

say "checking it can actually train"
./.venv/bin/python - <<'PY'
import torch
print(f"    torch {torch.__version__}, cuda available: {torch.cuda.is_available()}")
if torch.cuda.is_available():
    print(f"    device: {torch.cuda.get_device_name(0)}")
import jsbsim
print(f"    jsbsim {jsbsim.FGFDMExec(None).get_version()}")
PY

say "ready"
cat <<'NEXT'

    Next, upload the checkpoints this ladder needs as opponents, from your
    laptop (Git Bash), one line:

        scp -r models/competition/v4 models/competition/v5 root@<ip>:~/AGMCIS_APP/AIFCS/models/competition/

    Then start the queue:

        cd ~/AGMCIS_APP/AIFCS
        scripts/ladder.sh plans/ladder.yaml --shutdown

    --shutdown powers the machine off when the queue is empty, which is the
    difference between paying for the work and paying for the weekend.

NEXT
