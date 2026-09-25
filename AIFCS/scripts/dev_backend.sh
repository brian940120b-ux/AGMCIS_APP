#!/usr/bin/env bash
# Start the AIFCS backend with auto-reload.
# Success looks like: "Application startup complete." on http://127.0.0.1:8080
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib.sh
. "$ROOT/scripts/lib.sh"

PY="$(venv_python)" || { echo "No virtualenv found. Run: scripts/setup.sh"; exit 1; }
cd "$ROOT/backend"
# AIFCS listens on 8080, not 8000. The trading system this repository also
# holds runs its own FastAPI on 8000 under systemd, and two services that
# default to the same port on one host is a trap: whichever starts first wins,
# and if that is AIFCS then `systemctl restart agmcis` cannot bind and the
# trading dashboard goes down. Override with AIFCS_BACKEND_PORT if 8080 is
# taken by something else.
exec "$PY" -m uvicorn main:app --reload --host 127.0.0.1 --port "${AIFCS_BACKEND_PORT:-8080}"
