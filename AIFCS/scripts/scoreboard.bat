@echo off
REM Score every saved session against the same fixed opponents.
REM
REM ASCII only. CMD's batch parser and UTF-8 do not mix.
setlocal
cd /d "%~dp0.."
if exist ".venv\Scripts\python.exe" (
  set PY=.venv\Scripts\python.exe
) else (
  set PY=python
)
cd backend
"..\%PY%" -m competition.scoreboard %*
set EXITCODE=%ERRORLEVEL%
cd ..

if not "%EXITCODE%"=="0" (
  echo.
  echo Something went wrong. The lines above say what.

  REM Pause only when double-clicked. Git Bash sets MSYSTEM, so its absence
  REM means Explorer launched this and the window would vanish unread.
  if defined MSYSTEM exit /b %EXITCODE%
  pause
)
exit /b %EXITCODE%
