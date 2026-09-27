#!/usr/bin/env bash
# Work through a queue of experiments, detached, and optionally power the
# machine off at the end.
#
#   scripts/ladder.sh plans/ladder.yaml              run it, stay up
#   scripts/ladder.sh plans/ladder.yaml --shutdown   run it, then power off
#   scripts/ladder.sh plans/ladder.yaml --dry-run    print the commands only
#
# Detached with nohup, so closing the SSH session does not stop it. That is
# the whole point: start it, shut your laptop, read the log tomorrow.
set -euo pipefail

cd "$(dirname "$0")/.."

PLAN="${1:-plans/ladder.yaml}"
shift || true

SHUTDOWN=0
PASS_THROUGH=()
for arg in "$@"; do
  if [ "$arg" = "--shutdown" ]; then SHUTDOWN=1; else PASS_THROUGH+=("$arg"); fi
done

[ -f "$PLAN" ] || { echo "no plan at $PLAN"; exit 1; }
[ -x .venv/bin/python ] || { echo "no .venv — run scripts/cloud_setup.sh first"; exit 1; }

mkdir -p models/competition/_logs
LOG="models/competition/_logs/ladder-$(date +%Y%m%d-%H%M%S).log"

# A dry run is for reading, so it stays in the foreground.
if printf '%s\n' "${PASS_THROUGH[@]:-}" | grep -qx -- --dry-run; then
  exec ./.venv/bin/python -m competition.ladder "$PLAN" "${PASS_THROUGH[@]}"
fi

run() {
  ./.venv/bin/python -m competition.ladder "$PLAN" ${PASS_THROUGH[@]+"${PASS_THROUGH[@]}"}
  local code=$?
  echo
  echo "ladder finished with code $code at $(date -u +%FT%TZ)"
  if [ "$SHUTDOWN" = "1" ]; then
    echo "powering off in 60 seconds - ssh in and 'sudo shutdown -c' to stop that"
    sudo shutdown -h +1 || echo "could not power off; do it from the control panel"
  fi
}

export -f run 2>/dev/null || true
nohup bash -c "$(declare -f run); run" > "$LOG" 2>&1 &

echo
echo "  queue started, pid $!"
echo "  log:    $LOG"
echo "  watch:  tail -f $LOG"
echo "  stop:   touch models/competition/<name>/STOP"
[ "$SHUTDOWN" = "1" ] && echo "  the machine powers off when the queue is empty"
echo
echo "  This SSH session can be closed now."
echo
