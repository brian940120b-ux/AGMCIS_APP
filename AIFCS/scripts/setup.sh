#!/usr/bin/env bash
# One-time setup: Python virtualenv + frontend dependencies.
# Windows users: run this from Git Bash, not CMD or PowerShell.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib.sh
. "$ROOT/scripts/lib.sh"
cd "$ROOT"

PYTHON="$(find_python)" || {
  echo "Python $MIN_PYTHON or newer was not found."
  echo "需要 Python $MIN_PYTHON 以上版本。"
  exit 1
}

echo "==> Creating Python virtualenv (.venv) using $PYTHON"
"$PYTHON" -m venv .venv

VENV_PY="$(venv_python)" || { echo "The virtualenv was created but no interpreter was found in it."; exit 1; }
"$VENV_PY" -m pip install --upgrade pip
"$VENV_PY" -m pip install -r requirements-dev.txt

echo "==> Installing frontend dependencies"
cd frontend && npm install

echo
echo "Setup complete."
echo "  Everything at once : scripts/start.sh"
echo "  Backend only       : scripts/dev_backend.sh   -> http://127.0.0.1:8000/docs"
echo "  Frontend only      : scripts/dev_frontend.sh  -> http://127.0.0.1:5173"
