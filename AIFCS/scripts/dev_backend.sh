#!/usr/bin/env bash
# Start the AIFCS backend with auto-reload.
# Success looks like: "Application startup complete." on http://127.0.0.1:8000
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib.sh
. "$ROOT/scripts/lib.sh"

PY="$(venv_python)" || { echo "No virtualenv found. Run: scripts/setup.sh"; exit 1; }
cd "$ROOT/backend"
exec "$PY" -m uvicorn main:app --reload --host 127.0.0.1 --port "${AIFCS_BACKEND_PORT:-8000}"
