@echo off
REM AIFCS: fly a trained session against the organiser's host. The entry.
REM
REM   scripts\play.bat models\competition\v6 --selftest
REM   scripts\play.bat models\competition\v6 --listen-ip 192.168.1.3 --listen-port 8199 --host-ip 192.168.1.1 --host-port 8099
REM
REM Only the endpoints are flags; the aircraft comes from the session's card.
REM Runs until Ctrl+C. Run --selftest first, every time, before the hub.
REM
REM ASCII only. CMD's batch parser and UTF-8 do not mix, and a line of Chinese
REM in a .bat here was taken as a command to run. play.py prints in both
REM languages, because Python writes to the Windows console through an API
REM that ignores the codepage.
setlocal
cd /d "%~dp0.."

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo.
  echo Cannot find %PY%
  echo AIFCS is not installed yet. Run scripts\update.bat first.
  echo.

  REM Pause only when double-clicked. Git Bash sets MSYSTEM, so its absence
  REM means Explorer launched this and the window would vanish unread. From a
  REM terminal, `pause` eats the first character of whatever is typed next:
  REM three pastes of `scripts\ladder.bat` arrived as `cripts/ladder.bat`.
  if defined MSYSTEM exit /b 1
  pause
  exit /b 1
)

if "%~1"=="" (
  echo.
  echo Name the session to fly, e.g.  scripts\play.bat models\competition\v6 --selftest
  echo.
  if defined MSYSTEM exit /b 2
  pause
  exit /b 2
)

"%PY%" backend\competition\play.py %*
set EXITCODE=%ERRORLEVEL%

echo.

REM Pause only when double-clicked. Git Bash sets MSYSTEM, so its absence
REM means Explorer launched this and the window would vanish unread. From a
REM terminal, `pause` eats the first character of whatever is typed next:
REM three pastes of `scripts\ladder.bat` arrived as `cripts/ladder.bat`.
if defined MSYSTEM exit /b %EXITCODE%
pause
exit /b %EXITCODE%
