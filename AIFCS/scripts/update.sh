#!/usr/bin/env bash
#
# Bring this machine up to date and start AIFCS. One command.
#
#   ./scripts/update.sh              update, check, and start
#   ./scripts/update.sh --no-start   update and check only
#
# What it does, in order:
#   1. refuses to touch anything if you have uncommitted work
#   2. switches to the development branch and pulls
#   3. installs whatever Python and Node packages are new
#   4. runs `aifcs doctor` and stops if anything is actually broken
#   5. starts the backend and the dashboard
#
# 一個指令做完：更新、安裝、檢查、啟動。
# 使用期間請保持這個視窗開著 — 關掉視窗 AIFCS 就會停止。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib.sh
. "$ROOT/scripts/lib.sh"
cd "$ROOT"

BRANCH="${AIFCS_BRANCH:-claude/aifcs-flight-simulation-u32h56}"
START=1
[ "${1:-}" = "--no-start" ] && START=0

step()  { echo; echo "==> $*"; }
# Same shape as start.sh: a headline, then the lines that say what to do.
fail() {
  echo >&2
  echo "!!  $1" >&2
  shift
  for line in "$@"; do echo "    $line" >&2; done
  echo >&2
  exit 1
}

echo "AIFCS update"
echo "============"

# --- 1. Never clobber work you have not saved ------------------------------
step "Checking for unsaved changes / 檢查有沒有未儲存的修改"
if ! git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  fail "This is not a git checkout, so there is nothing to update." \
       "這不是一個 git 目錄，沒有東西可以更新。" \
       "Clone it instead: git clone https://github.com/brian940120b-ux/AGMCIS_APP.git"
fi

# Only edits to tracked files can be clobbered by a fast-forward pull. Untracked
# files are left alone by git, so they are worth mentioning but never a reason to
# stop — leftovers from a failed download used to block the whole update here.
if [ -n "$(git -C "$ROOT" status --porcelain --untracked-files=no -- "$ROOT" 2>/dev/null)" ]; then
  echo
  git -C "$ROOT" status --short --untracked-files=no -- "$ROOT"
  fail "You have changes in AIFCS/ that are not committed." \
       "AIFCS/ 底下有還沒提交的修改，更新會蓋掉它們。" \
       "Save them:    git add -A AIFCS && git commit -m 'my changes'" \
       "Or throw them away: git checkout -- AIFCS"
fi

UNTRACKED="$(git -C "$ROOT" ls-files --others --exclude-standard --directory -- "$ROOT" 2>/dev/null || true)"
if [ -n "$UNTRACKED" ]; then
  echo "    Nothing unsaved. Ignoring these extra files, which git does not track:"
  echo "    沒有未儲存的修改。以下是多出來的檔案，更新不會動到它們："
  echo "$UNTRACKED" | head -10 | sed 's|^|      |'
else
  echo "    Nothing unsaved. / 沒有未儲存的修改。"
fi

# --- 2. Get the new code ----------------------------------------------------
step "Fetching the latest code / 取得最新的程式碼"
git -C "$ROOT" fetch origin "$BRANCH" || fail \
  "Could not reach GitHub. / 連不到 GitHub。" \
  "Check the network, then run this again."

BEFORE="$(git -C "$ROOT" rev-parse HEAD)"
git -C "$ROOT" checkout "$BRANCH" 2>/dev/null || git -C "$ROOT" checkout -b "$BRANCH" "origin/$BRANCH"
git -C "$ROOT" pull --ff-only origin "$BRANCH" || fail \
  "The pull could not fast-forward. / 無法直接更新。" \
  "Your copy has commits that GitHub does not. Ask before forcing anything."
AFTER="$(git -C "$ROOT" rev-parse HEAD)"

if [ "$BEFORE" = "$AFTER" ]; then
  echo "    Already up to date. / 已經是最新版。"
else
  COUNT="$(git -C "$ROOT" rev-list --count "$BEFORE..$AFTER" 2>/dev/null || echo "?")"
  echo "    Updated — $COUNT new commit(s). / 更新了 $COUNT 個提交。"
  git -C "$ROOT" log --oneline "$BEFORE..$AFTER" -- "$ROOT" | head -10 | sed 's/^/      /'
fi

# --- 3. Install anything new ------------------------------------------------
step "Installing dependencies / 安裝相依套件"
PYTHON="$(find_python)" || fail \
  "Python $MIN_PYTHON or newer was not found. / 找不到 Python $MIN_PYTHON 以上。" \
  "Install it from python.org, ticking \"Add python.exe to PATH\"."

if ! venv_python >/dev/null 2>&1; then
  echo "    Creating the virtualenv (first time, a few minutes)…"
  "$PYTHON" -m venv .venv
fi
VENV_PY="$(venv_python)"

# Quiet unless something actually installs, so a no-op update stays readable.
"$VENV_PY" -m pip install --quiet --upgrade pip
"$VENV_PY" -m pip install --quiet -r requirements-dev.txt

# Whoever installed the optional RL stack opted into it; keep it current too,
# rather than leaving it pinned to whatever requirements-ml.txt said that day.
# It is never installed here — that is a multi-GB download and a deliberate act.
if "$VENV_PY" -c "import gymnasium" >/dev/null 2>&1; then
  echo "    Updating the reinforcement-learning stack you have installed…"
  "$VENV_PY" -m pip install --quiet -r requirements-ml.txt
fi
echo "    Python packages up to date."

if ! command -v npm >/dev/null 2>&1; then
  fail "Node.js was not found. / 找不到 Node.js。" "Install it from nodejs.org, then run this again."
fi
(cd frontend && npm install --silent)
echo "    Frontend packages up to date."

# --- 4. Check it before claiming it works -----------------------------------
step "Checking the installation / 檢查安裝"
if ! "$VENV_PY" backend/cli.py doctor; then
  fail "Something is wrong — see the FAIL lines above." \
       "上面標 FAIL 的就是壞掉的部分。" \
       "Send those lines back and they can be fixed."
fi

# --- 5. Start ---------------------------------------------------------------
if [ "$START" -eq 0 ]; then
  echo
  echo "Ready. Start it with: ./scripts/start.sh"
  echo "準備好了。用 ./scripts/start.sh 啟動。"
  exit 0
fi

step "Starting AIFCS / 啟動 AIFCS"
exec "$ROOT/scripts/start.sh"
