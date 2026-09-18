#!/usr/bin/env bash
# Run every quality gate: lint, type check and tests, backend and frontend.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib.sh
. "$ROOT/scripts/lib.sh"
cd "$ROOT"

VENV="$(venv_bin)" || { echo "No virtualenv found. Run: scripts/setup.sh"; exit 1; }
PY="$(venv_python)"

echo "==> ruff (lint)";        "$VENV/ruff" check backend
echo "==> ruff (format)";      "$VENV/ruff" format --check backend
echo "==> mypy (types)";       "$VENV/mypy"
echo "==> pytest (backend)";   (cd backend && "$PY" -m pytest)
echo "==> eslint (frontend)";  (cd frontend && npm run --silent lint)
echo "==> tsc (frontend)";     (cd frontend && npm run --silent typecheck)
echo
echo "All checks passed."
