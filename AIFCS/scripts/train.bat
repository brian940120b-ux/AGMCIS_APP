@echo off
REM AIFCS competition training. Double-click to start, or to carry on.
REM
REM Resuming is the default and needs no flag: the session on disk knows how
REM many steps it has had, and --timesteps is the total to reach rather than an
REM amount to add. Stopping with Ctrl+C, closing this window, or shutting the
REM machine down costs at most one checkpoint interval.
REM
REM ASCII only. CMD's batch parser and UTF-8 do not mix, and a line of Chinese
REM in this file was taken as a command to run. What a person reads in Chinese
REM is printed by train.py, because Python writes to the Windows console
REM through an API that does not go through the codepage.
setlocal
cd /d "%~dp0.."

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo.
  echo Cannot find %PY%
  echo AIFCS is not installed yet. Run scripts\update.bat first.
  echo.
  pause
  exit /b 1
)

REM Defaults for an overnight run. Anything typed after the file name wins,
REM because argparse takes the last value it sees:
REM     train.bat --timesteps 20000000 --name run2
set "ARGS=--name run1 --algorithm sac --timesteps 5000000 --workers 8 --device auto"

"%PY%" backend\competition\train.py %ARGS% %*
set EXITCODE=%ERRORLEVEL%

echo.
if "%EXITCODE%"=="0" (
  echo Done.
) else if "%EXITCODE%"=="130" (
  echo Stopped by you. Progress is saved.
) else (
  echo Something went wrong - the lines above say what.
)
pause
exit /b %EXITCODE%
