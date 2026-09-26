@echo off
REM Check everything the upgrades added, on this machine. Double-click this.
REM
REM About two minutes. Nothing is saved and nothing existing is touched.
REM
REM ASCII only. CMD's batch parser and UTF-8 do not mix, and a line of Chinese
REM in a .bat here was taken as a command to run. tryout.py prints in both
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
  pause
  exit /b 1
)

"%PY%" backend\competition\tryout.py %*
set EXITCODE=%ERRORLEVEL%

echo.
pause
exit /b %EXITCODE%
