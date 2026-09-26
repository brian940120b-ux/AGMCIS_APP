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

# How the dashboard is served.
#
#   built  the backend serves frontend/dist. One process, no Node at run time,
#          and none of Vite's dependency pre-bundling — which is what failed on
#          a laptop that ran out of Windows file handles trying to do it.
#   dev    Vite's dev server on its own port, with hot reload. For working on
#          the frontend, which is not what most starts are for.
#
# The default is "built" because the common case is using the platform, not
# editing it. The build itself still needs Node; serving the result does not.
DASHBOARD_MODE="${AIFCS_DASHBOARD:-built}"
case "$DASHBOARD_MODE" in
  built|dev) ;;
  *) echo "AIFCS_DASHBOARD must be 'built' or 'dev', not '$DASHBOARD_MODE'." >&2; exit 2 ;;
esac
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

# Always prints a section, even when there is nothing in the file. "It wrote
# nothing at all" is the most useful line in such a report, not the absence of
# one — a silent return is how a failure reached its reader with no evidence.
show_log() {
  local path="$1" label="$2"
  echo
  echo "--- $label ---"
  if [ ! -f "$path" ]; then
    echo "    (no file at $path — the process never wrote anything)"
  elif [ ! -s "$path" ]; then
    echo "    (the file is empty — the process started but printed nothing)"
  else
    tail -20 "$path" | sed 's/^/    /'
  fi
}

port_busy() {
  # Returns success when something already answers on the port.
  curl -s -o /dev/null -m 2 "http://127.0.0.1:$1" 2>/dev/null
}

echo "==> AIFCS — AI Flight Command & Simulation Platform"
echo

# --- 1. Prerequisites -------------------------------------------------------
missing=""
# Node builds the dashboard; it does not serve it. A machine with a built
# dashboard and no Node can still run the platform, so the requirement follows
# what actually has to happen rather than the mode's name.
if [ "$DASHBOARD_MODE" = "dev" ] || ! [ -f "$ROOT/frontend/dist/index.html" ]; then
  command -v node >/dev/null 2>&1 || missing="$missing Node.js"
  command -v npm  >/dev/null 2>&1 || missing="$missing npm"
fi

# Windows installs the interpreter as `python`, macOS/Linux as `python3`.
PYTHON="$(find_python)" || missing="$missing Python$MIN_PYTHON+"

if [ -n "$missing" ]; then
  fail "Missing:$missing / 缺少這些程式:$missing" \
       "Install them, then run this script again. / 安裝後再執行一次。"
fi

echo "    Python  $("$PYTHON" --version 2>&1 | cut -d' ' -f2)  ($PYTHON)"
if command -v node >/dev/null 2>&1; then
  echo "    Node    $(node --version)"
else
  echo "    Node    not installed — serving the dashboard that is already built"
fi

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
if [ "$DASHBOARD_MODE" = "dev" ] && port_busy "$FRONTEND_PORT"; then
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

# --- 3b. Dashboard bundle ---------------------------------------------------
# Before the backend, because the backend decides at startup whether there is a
# bundle to serve.
if [ "$DASHBOARD_MODE" = "built" ]; then
  DIST_INDEX="$ROOT/frontend/dist/index.html"
  needs_build=""
  if [ ! -f "$DIST_INDEX" ]; then
    needs_build="no bundle yet"
  else
    # Anything that can change the output, newer than the output.
    for source in "$ROOT/frontend/src" "$ROOT/frontend/index.html" \
                  "$ROOT/frontend/package.json" "$ROOT/frontend/vite.config.ts"; do
      [ -e "$source" ] || continue
      if [ -n "$(find "$source" -newer "$DIST_INDEX" -print -quit 2>/dev/null)" ]; then
        needs_build="the dashboard changed since it was last built"
        break
      fi
    done
  fi

  if [ -n "$needs_build" ]; then
    echo
    echo "==> Building the dashboard / 打包前端（$needs_build）…"
    echo "    This happens once. Later starts reuse it. 只有這次要等，之後會直接用。"
    if ! (cd frontend && npm run build); then
      fail "Dashboard build failed — the lines above say why." \
           "前端打包失敗，原因在上面。"
    fi
  else
    echo "    Dashboard bundle is up to date / 前端已是最新，不用重新打包"
  fi
fi

# --- 4. Backend -------------------------------------------------------------
echo
echo "==> Starting simulation backend / 啟動模擬引擎…"
# In dev mode the dev server is the dashboard, so the backend does not also
# serve whatever bundle happens to be on disk: two dashboards, one of them
# stale, is a confusing thing to debug.
[ "$DASHBOARD_MODE" = "dev" ] && export AIFCS_SERVE_DASHBOARD=0
(cd backend && exec "$VENV_PY" -m uvicorn main:app \
  --host 127.0.0.1 --port "$BACKEND_PORT") > "$BACKEND_LOG" 2>&1 &
backend_pid=$!

# 30 seconds was too short and the failure looked like a broken backend. The
# startup path imports torch to report whether training is available, and a
# CUDA build's first import — cold page cache, antivirus reading every DLL —
# takes far longer than that on a laptop. Measured here: 30s timeout on the
# first start after a restart, 2s on the next one, same code.
#
# So: wait long enough for the slow case, and say what is being waited for
# instead of going silent. Waiting costs nothing when the start is quick.
BACKEND_TIMEOUT_S="${AIFCS_BACKEND_TIMEOUT_S:-180}"
waited=0
until curl -sf -m 2 "http://127.0.0.1:$BACKEND_PORT/api/health" >/dev/null 2>&1; do
  kill -0 "$backend_pid" 2>/dev/null || { cat "$BACKEND_LOG"; fail "Backend exited during startup." "後端啟動失敗，訊息在上面。"; }
  if [ "$waited" -ge "$BACKEND_TIMEOUT_S" ]; then
    show_log "$BACKEND_LOG" "backend log"
    fail "Backend did not become healthy in ${BACKEND_TIMEOUT_S}s." \
         "後端 ${BACKEND_TIMEOUT_S} 秒內沒有啟動成功。"
  fi
  # Silence for three minutes reads as a hang, so account for the time.
  if [ "$waited" -eq 20 ]; then
    echo "    Still starting — the first run loads PyTorch, which is slow."
    echo "    第一次啟動要載入 PyTorch，比較久，請等一下。"
  elif [ "$waited" -gt 20 ] && [ $((waited % 30)) -eq 0 ]; then
    echo "    …still waiting (${waited}s of ${BACKEND_TIMEOUT_S}s)"
  fi
  sleep 1
  waited=$((waited + 1))
done
echo "    Backend ready — http://127.0.0.1:$BACKEND_PORT/docs"

# --- 5. Dashboard -----------------------------------------------------------
if [ "$DASHBOARD_MODE" = "built" ]; then
  # Already being served by the backend, on the same origin as the API — which
  # is why the frontend's relative /api and /ws paths need no proxy here.
  FRONTEND_URL="http://127.0.0.1:$BACKEND_PORT"
else
  echo "==> Starting dashboard / 啟動儀表板…"
  (cd frontend && exec npm run dev -- --port "$FRONTEND_PORT") > "$FRONTEND_LOG" 2>&1 &
  frontend_pid=$!

  # Two names for the same machine, because they are not always the same
  # address. "localhost" resolves to the IPv6 loopback first on some Windows
  # setups and the dev server may be listening only on IPv4, or the other way
  # round. Whichever answers is the one the browser is told to use.
  #
  # The deadline is generous for the same reason the backend's is: on a first
  # run Vite pre-bundles dependencies after saying it is ready, and that step
  # reads thousands of files.
  FRONTEND_TIMEOUT_S="${AIFCS_FRONTEND_TIMEOUT_S:-180}"
  FRONTEND_URL=""
  waited=0
  while [ "$waited" -lt "$FRONTEND_TIMEOUT_S" ]; do
    for candidate in "http://127.0.0.1:$FRONTEND_PORT" "http://localhost:$FRONTEND_PORT"; do
      if curl -sf -m 2 "$candidate" >/dev/null 2>&1; then FRONTEND_URL="$candidate"; break 2; fi
    done
    kill -0 "$frontend_pid" 2>/dev/null || { show_log "$FRONTEND_LOG" "dashboard log"; fail "Dashboard exited during startup." "前端啟動失敗，訊息在上面。"; }
    if [ "$waited" -eq 20 ]; then
      echo "    Still starting — the first run bundles the dashboard's packages."
      echo "    第一次啟動要打包前端套件，比較久，請等一下。"
    elif [ "$waited" -gt 20 ] && [ $((waited % 30)) -eq 0 ]; then
      echo "    …still waiting (${waited}s of ${FRONTEND_TIMEOUT_S}s)"
    fi
    sleep 1
    waited=$((waited + 1))
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
    show_log "$FRONTEND_LOG" "dashboard log"
    echo
    echo "    The dev server is not the only way to run the dashboard."
    echo "    Try without it:  AIFCS_DASHBOARD=built ./scripts/start.sh"
    echo "    不用開發伺服器也能跑，上面那行試試看。"
    fail "Dashboard did not answer in ${FRONTEND_TIMEOUT_S}s." "前端 ${FRONTEND_TIMEOUT_S} 秒內沒有回應。"
  fi
fi

# --- 6. Ready ---------------------------------------------------------------
# Set above: the backend's own address in built mode, or whichever of the two
# names the dev server answered on.
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
