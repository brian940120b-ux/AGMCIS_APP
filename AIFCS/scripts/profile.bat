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
  pause
  exit /b 1
)

set "TARGET=%~1"
if "%TARGET%"=="" set "TARGET=models\competition\v4"

REM Do not run this while training: they would be timing each other.
tasklist /fi "IMAGENAME eq python.exe" 2>nul | find /i "python.exe" >nul
if not errorlevel 1 (
  echo.
  echo   A python process is already running - probably training.
  echo   Timing it now would measure the two fighting over the machine.
  echo   Stop training first:  scripts\train_stop.bat --name ^<session^>
  echo.
  pause
  exit /b 1
)

"%PY%" -m competition.timing "%TARGET%" %2 %3 %4 %5
echo.
pause
