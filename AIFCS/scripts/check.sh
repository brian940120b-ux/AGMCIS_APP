#!/usr/bin/env bash
# Run every quality gate: lint, type check and tests, backend and frontend.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
VENV="$ROOT/.venv/bin"

echo "==> ruff (lint)";        "$VENV/ruff" check backend
echo "==> ruff (format)";      "$VENV/ruff" format --check backend
echo "==> mypy (types)";       "$VENV/mypy"
echo "==> pytest (backend)";   (cd backend && "$VENV/python" -m pytest)
echo "==> eslint (frontend)";  (cd frontend && npm run --silent lint)
echo "==> tsc (frontend)";     (cd frontend && npm run --silent typecheck)
echo
echo "All checks passed."
