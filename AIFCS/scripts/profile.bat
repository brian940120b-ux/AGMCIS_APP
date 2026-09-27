@echo off
REM AIFCS: find out where the training time goes, before buying anything.
REM
REM Two timings and a subtraction: the simulation alone, then the same
REM steps through learn. The difference is what learning costs.
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

set "TARGET=%~1"
if "%TARGET%"=="" set "TARGET=models\competition\v4"

REM Full path to find.exe. Launched from Git Bash, PATH puts GNU find
REM first and a bare `find /i` fails with "No such file or directory",
REM which then reads as the check having passed.
tasklist /fi "IMAGENAME eq python.exe" 2>nul | %SystemRoot%\System32\find.exe /i "python.exe" >nul
if not errorlevel 1 (
  echo.
  echo   A python process is already running - probably training.
  echo   Timing it now would measure the two fighting over the machine.
  echo   Stop training first:  scripts\train_stop.bat --name ^<session^>
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
REM Without this the module is simply not found, from a directory that
REM looks like the right one.
set "PYTHONPATH=%CD%\backend;%PYTHONPATH%"
"%PY%" -m competition.timing "%TARGET%" %2 %3 %4 %5
echo.

REM Pause only when double-clicked. Git Bash sets MSYSTEM, so its absence
REM means Explorer launched this and the window would vanish unread. From a
REM terminal, `pause` eats the first character of whatever is typed next:
REM three pastes of `scripts\ladder.bat` arrived as `cripts/ladder.bat`.
if defined MSYSTEM exit /b %ERRORLEVEL%
pause
