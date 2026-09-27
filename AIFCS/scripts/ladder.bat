@echo off
REM AIFCS: work through a queue of experiments overnight, on this machine.
REM
REM The Linux twin of this is ladder.sh, for a rented box. It turned out
REM not to be needed: 93% of a training step is the gradient update and the
REM GPU is idle at 33%, so a faster machine is not the purchase. The queue
REM is still the point - it is what stops each generation waiting for
REM somebody to start the next one.
REM
REM   ladder.bat                     run plans\ladder.yaml
REM   ladder.bat plans\other.yaml    run another plan
REM   ladder.bat --dry-run           print what it would do
REM
REM Stop it with:  scripts\train_stop.bat --name <whichever step is running>
REM
REM ASCII only. CMD batch and UTF-8 do not mix.
setlocal
cd /d "%~dp0.."

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo.
  echo Cannot find %PY% - run scripts\update.bat first.
  echo.

  REM Pause only when double-clicked. Git Bash sets MSYSTEM, so its absence
  REM means Explorer launched this and the window would vanish unread. From a
  REM terminal, `pause` eats the first character of whatever is typed next:
  REM three pastes of `scripts\ladder.bat` arrived as `cripts/ladder.bat`.
  if defined MSYSTEM exit /b 1
  pause
  exit /b 1
)

set "PLAN=%~1"
if "%PLAN%"=="" set "PLAN=plans\ladder.yaml"
if "%PLAN%"=="--dry-run" set "PLAN=plans\ladder.yaml"
if not exist "%PLAN%" (
  echo.
  echo No plan at %PLAN%
  echo.

  REM Pause only when double-clicked. Git Bash sets MSYSTEM, so its absence
  REM means Explorer launched this and the window would vanish unread. From a
  REM terminal, `pause` eats the first character of whatever is typed next:
  REM three pastes of `scripts\ladder.bat` arrived as `cripts/ladder.bat`.
  if defined MSYSTEM exit /b 1
  pause
  exit /b 1
)

REM competition/ lives under backend/, so -m needs backend on the path.
set "PYTHONPATH=%CD%\backend;%PYTHONPATH%"
"%PY%" -m competition.ladder "%PLAN%" --python "%CD%\%PY%" %*

echo.

REM Pause only when double-clicked. Git Bash sets MSYSTEM, so its absence
REM means Explorer launched this and the window would vanish unread. From a
REM terminal, `pause` eats the first character of whatever is typed next:
REM three pastes of `scripts\ladder.bat` arrived as `cripts/ladder.bat`.
if defined MSYSTEM exit /b %ERRORLEVEL%
pause
