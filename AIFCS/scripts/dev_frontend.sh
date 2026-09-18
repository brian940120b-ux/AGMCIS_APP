#!/usr/bin/env bash
# Start the AIFCS dashboard dev server.
# Success looks like: "Local: http://localhost:5173/"
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/frontend"
[ -d node_modules ] || npm install
exec npm run dev
