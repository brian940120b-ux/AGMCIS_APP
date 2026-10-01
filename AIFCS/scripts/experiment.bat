@echo off
REM Experiments, written down: new / show / running / result / list.
REM   scripts\experiment.bat list
REM   scripts\experiment.bat show EXP-001-official-sac-baseline
REM   scripts\experiment.bat result EXP-001-official-sac-baseline --scoreboard results\exp001.json --decision keep
REM
REM ASCII only. CMD's batch parser and UTF-8 do not mix.
setlocal
cd /d "%~dp0.."
if exist ".venv\Scripts\python.exe" (
  set PY=.venv\Scripts\python.exe
) else (
  set PY=python
)
REM Run from the AIFCS root, as a file, so that paths typed on the command
REM line (models\competition\v6, results\exp002.json) mean what they say.
REM An earlier version changed into backend\ first and looked for every
REM session under backend\models\, which does not exist.
"%PY%" backend\competition\experiments.py %*
set EXITCODE=%ERRORLEVEL%

if not "%EXITCODE%"=="0" (
  echo.
  echo Something went wrong. The lines above say what.

  REM Pause only when double-clicked. Git Bash sets MSYSTEM, so its absence
  REM means Explorer launched this and the window would vanish unread.
  if defined MSYSTEM exit /b %EXITCODE%
  pause
)
exit /b %EXITCODE%
