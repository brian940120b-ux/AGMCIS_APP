#!/usr/bin/env bash
#
# AIFCS one-command launcher / 一鍵啟動
#
#   ./scripts/start.sh
#
# Checks prerequisites, installs anything missing, starts the backend and the
# dashboard together, and stops both cleanly on Ctrl+C.

set -uo pipefail

# Job control puts each background job in its own process group, so cleanup can
# kill the whole tree. Without it, `npm run dev` dies but the Vite process it
# spawned survives and keeps holding the port.
set -m

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib.sh
. "$ROOT/scripts/lib.sh"
cd "$ROOT"

# AIFCS listens on 8080, not 8000. The trading system this repository also
# holds runs its own FastAPI on 8000 under systemd, and two services that
# default to the same port on one host is a trap: whichever starts first wins,
# and if that is AIFCS then `systemctl restart agmcis` cannot bind and the
# trading dashboard goes down. Override with AIFCS_BACKEND_PORT if 8080 is
# taken by something else.
BACKEND_PORT="${AIFCS_BACKEND_PORT:-8080}"
FRONTEND_PORT="${AIFCS_FRONTEND_PORT:-5173}"
LOG_DIR="$ROOT/data/telemetry"
BACKEND_LOG="$LOG_DIR/backend.out"
FRONTEND_LOG="$LOG_DIR/frontend.out"

backend_pid=""
frontend_pid=""

# Terminate a job and every process it spawned.
stop_tree() {
  local pid="$1"
  [ -z "$pid" ] && return
  kill -TERM "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
}

cleanup() {
  # Nothing was started (an early prerequisite check failed) — stay quiet, so a
  # "Stopping…" message never muddies the error the user needs to read.
  if [ -z "$backend_pid" ] && [ -z "$frontend_pid" ]; then
    return
  fi
  echo
  echo "==> Stopping AIFCS / 正在關閉…"
  stop_tree "$frontend_pid"
  stop_tree "$backend_pid"

  # Give them a moment, then make sure nothing survived.
  sleep 1
  kill -KILL "-$frontend_pid" 2>/dev/null
  kill -KILL "-$backend_pid" 2>/dev/null
  wait 2>/dev/null
  echo "    Stopped. / 已關閉。"
}
trap cleanup INT TERM EXIT

fail() {
  echo
  echo "!!  $1"
  echo "    $2"
  echo
  exit 1
}

port_busy() {
  # Returns success when something already answers on the port.
  curl -s -o /dev/null -m 2 "http://127.0.0.1:$1" 2>/dev/null
}

echo "==> AIFCS — AI Flight Command & Simulation Platform"
echo

# --- 1. Prerequisites -------------------------------------------------------
missing=""
command -v node >/dev/null 2>&1 || missing="$missing Node.js"
command -v npm  >/dev/null 2>&1 || missing="$missing npm"

# Windows installs the interpreter as `python`, macOS/Linux as `python3`.
PYTHON="$(find_python)" || missing="$missing Python$MIN_PYTHON+"

if [ -n "$missing" ]; then
  fail "Missing:$missing / 缺少這些程式:$missing" \
       "Install them, then run this script again. / 安裝後再執行一次。"
fi

echo "    Python  $("$PYTHON" --version 2>&1 | cut -d' ' -f2)  ($PYTHON)"
echo "    Node    $(node --version)"

# --- 2. Ports ---------------------------------------------------------------
# This repository holds two systems: the AGMCIS trading platform at the top
# level and AIFCS underneath it. They are separate programs with separate
# virtualenvs and separate databases, and the only thing they can collide over
# is a port. AIFCS moved to 8080 so they cannot, but if the trading system is
# on this machine it is worth naming rather than reporting a bare conflict.
if [ -f "$ROOT/../main.py" ] && [ -f "$ROOT/../database_service.py" ]; then
  echo "    Note: the AGMCIS trading system shares this repository."
  echo "          AIFCS uses port $BACKEND_PORT; the trading system uses 8000. They do not overlap."
  if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet agmcis 2>/dev/null; then
    echo "          agmcis.service is RUNNING — leave it alone; AIFCS will not touch it."
    echo "          交易系統正在執行中 — AIFCS 不會動到它。"
  fi
fi

if port_busy "$BACKEND_PORT"; then
  if [ "$BACKEND_PORT" = "8000" ]; then
    fail "Port 8000 is in use, and 8000 is the trading system's port. / 連接埠 8000 是交易系統在用的。" \
         "Do not take it. Run without AIFCS_BACKEND_PORT set and AIFCS will use 8080."
  fi
  fail "Port $BACKEND_PORT is already in use. / 連接埠 $BACKEND_PORT 已被占用。" \
       "Close the other program, or run: AIFCS_BACKEND_PORT=8081 ./scripts/start.sh"
fi
if port_busy "$FRONTEND_PORT"; then
  fail "Port $FRONTEND_PORT is already in use. / 連接埠 $FRONTEND_PORT 已被占用。" \
       "Close the other program, or run: AIFCS_FRONTEND_PORT=5174 ./scripts/start.sh"
fi

# --- 3. Install anything missing -------------------------------------------
if ! venv_python >/dev/null 2>&1; then
  echo
  echo "==> First run: installing backend dependencies / 首次執行，安裝後端套件…"
  "$PYTHON" -m venv .venv || fail "Could not create the virtualenv." "無法建立虛擬環境。"
  VENV_PY="$(venv_python)" || fail "The virtualenv has no interpreter." "虛擬環境建立失敗。"
  "$VENV_PY" -m pip install --upgrade pip --quiet
  "$VENV_PY" -m pip install -r requirements-dev.txt --quiet \
    || fail "Backend dependency install failed." "後端套件安裝失敗。"
fi

VENV_PY="$(venv_python)" || fail "No virtualenv interpreter found." "找不到虛擬環境。"

if [ ! -d "$ROOT/frontend/node_modules" ]; then
  echo "==> First run: installing dashboard dependencies / 首次執行，安裝前端套件…"
  (cd frontend && npm install --no-fund --no-audit) \
    || fail "Dashboard dependency install failed." "前端套件安裝失敗。"
fi

mkdir -p "$LOG_DIR"

# --- 4. Backend -------------------------------------------------------------
echo
echo "==> Starting simulation backend / 啟動模擬引擎…"
(cd backend && exec "$VENV_PY" -m uvicorn main:app \
  --host 127.0.0.1 --port "$BACKEND_PORT") > "$BACKEND_LOG" 2>&1 &
backend_pid=$!

for _ in $(seq 1 60); do
  curl -sf -m 2 "http://127.0.0.1:$BACKEND_PORT/api/health" >/dev/null 2>&1 && break
  kill -0 "$backend_pid" 2>/dev/null || { cat "$BACKEND_LOG"; fail "Backend exited during startup." "後端啟動失敗，訊息在上面。"; }
  sleep 0.5
done

if ! curl -sf -m 2 "http://127.0.0.1:$BACKEND_PORT/api/health" >/dev/null 2>&1; then
  tail -20 "$BACKEND_LOG"
  fail "Backend did not become healthy in 30s." "後端 30 秒內沒有啟動成功。"
fi
echo "    Backend ready — http://127.0.0.1:$BACKEND_PORT/docs"

# --- 5. Dashboard -----------------------------------------------------------
echo "==> Starting dashboard / 啟動儀表板…"
(cd frontend && exec npm run dev -- --port "$FRONTEND_PORT") > "$FRONTEND_LOG" 2>&1 &
frontend_pid=$!

# Two names for the same machine, because they are not always the same address.
# "localhost" resolves to the IPv6 loopback first on some Windows setups and the
# dev server may be listening only on IPv4, or the other way round. Whichever
# answers is the one the browser is told to use.
FRONTEND_URL=""
for _ in $(seq 1 60); do
  for candidate in "http://127.0.0.1:$FRONTEND_PORT" "http://localhost:$FRONTEND_PORT"; do
    if curl -sf -m 2 "$candidate" >/dev/null 2>&1; then FRONTEND_URL="$candidate"; break 2; fi
  done
  kill -0 "$frontend_pid" 2>/dev/null || { cat "$FRONTEND_LOG"; fail "Dashboard exited during startup." "前端啟動失敗，訊息在上面。"; }
  sleep 0.5
done

if [ -z "$FRONTEND_URL" ]; then
  # A timeout that says only "it did not start" is useless next to a log that
  # says the server is ready — which is exactly the pair this printed once.
  # So say what was actually tried and what came back.
  echo
  echo "    The dashboard did not answer. What was tried:"
  for candidate in "http://127.0.0.1:$FRONTEND_PORT" "http://localhost:$FRONTEND_PORT"; do
    code="$(curl -s -m 2 -o /dev/null -w '%{http_code}' "$candidate" 2>/dev/null)"
    status=$?
    echo "      $candidate  ->  curl exit $status, HTTP ${code:-none}"
  done
  if command -v netstat >/dev/null 2>&1; then
    echo "    Listening on $FRONTEND_PORT:"
    netstat -an 2>/dev/null | grep -E "[:.]$FRONTEND_PORT\b" | head -5 | sed 's/^/      /'
  fi
  echo
  tail -20 "$FRONTEND_LOG"
  fail "Dashboard did not answer in 30s." "前端 30 秒內沒有回應。"
fi

# --- 6. Ready ---------------------------------------------------------------
# Whichever name answered above. Not assumed: "localhost" and "127.0.0.1" are
# the same machine and not always the same address.
URL="$FRONTEND_URL"
echo
echo "================================================================"
echo "  AIFCS is running.  AIFCS 已啟動。"
echo
echo "  Open this in your browser / 用瀏覽器打開："
echo "      $URL"
echo
echo "  KEEP THIS WINDOW OPEN while you use AIFCS."
echo "  使用期間請保持這個視窗開著 — 關掉視窗 AIFCS 就會停止。"
echo
echo "  To stop: press Ctrl+C here.  要關閉：在這個視窗按 Ctrl+C。"
echo "================================================================"
echo

# Open the browser automatically where the platform supports it.
if command -v open >/dev/null 2>&1; then
  open "$URL" 2>/dev/null || true          # macOS
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$URL" 2>/dev/null || true      # Linux
elif command -v powershell.exe >/dev/null 2>&1; then
  powershell.exe -NoProfile start "$URL" 2>/dev/null || true   # Windows (Git Bash)
fi

wait
