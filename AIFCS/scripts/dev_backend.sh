#!/usr/bin/env bash
# Start the AIFCS backend with auto-reload.
# Success looks like: "Application startup complete." on http://127.0.0.1:8000
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || { echo "No virtualenv found. Run: scripts/setup.sh"; exit 1; }
cd "$ROOT/backend"
exec "$PY" -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
