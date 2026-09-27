@echo off
REM AIFCS: what the background training is doing right now.
REM
REM Reads the newest log rather than asking Windows about windows: a
REM tasklist window-title filter misses a run started from an ordinary
REM terminal, and a run that has stopped leaves its log behind to read.
REM
REM ASCII only. CMD batch and UTF-8 do not mix.
setlocal
cd /d "%~dp0.."

set "LOGDIR=models\competition\_logs"
echo.
if not exist "%LOGDIR%" (
  echo   Nothing has been started in the background yet.
  echo   Start one with:  scripts\train_background.bat --name v5
  echo.
  pause
  exit /b 0
)

set "LATEST="
for /f "delims=" %%f in ('dir /b /o-d "%LOGDIR%\train-*.log" 2^>nul') do if not defined LATEST set "LATEST=%%f"

if not defined LATEST (
  echo   No logs in %LOGDIR% yet.
  echo.
  pause
  exit /b 0
)

REM A python.exe alive at all is the honest signal available here. The
REM worker processes are python too, so this counts more than one.
tasklist /fi "IMAGENAME eq python.exe" 2>nul | find /i "python.exe" >nul
if errorlevel 1 (
  echo   RUNNING: no python process found - the run has finished or died
) else (
  echo   RUNNING: yes - python is working
)
echo.
echo   log: %LOGDIR%\%LATEST%
echo   ---------------------------------------------------------------
powershell -NoProfile -Command "Get-Content -Tail 15 -LiteralPath '%LOGDIR%\%LATEST%'"
echo   ---------------------------------------------------------------
echo.
echo   The percentage line appears every 25,000 steps - about every five
echo   minutes at 75 steps/s. No new line for 20 minutes means look closer.
echo.
pause
