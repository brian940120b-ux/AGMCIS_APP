#!/usr/bin/env bash
# One-time setup: Python virtualenv + frontend dependencies.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "==> Creating Python virtualenv (.venv)"
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements-dev.txt

echo "==> Installing frontend dependencies"
cd frontend && npm install

echo
echo "Setup complete."
echo "  Backend : scripts/dev_backend.sh   -> http://127.0.0.1:8000/docs"
echo "  Frontend: scripts/dev_frontend.sh  -> http://127.0.0.1:5173"
