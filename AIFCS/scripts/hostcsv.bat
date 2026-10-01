@echo off
REM AIFCS: read the organiser's host CSV and check our scoring against its columns.
REM
REM   scripts\hostcsv.bat "C:\AirCombat_Train_Test\D.<folder>\<record>.csv"
REM
REM Read-only. Pass the CSV path in quotes: the folder name has Chinese in it.
REM
REM ASCII only. CMD's batch parser and UTF-8 do not mix.
setlocal
cd /d "%~dp0.."

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo.
  echo Cannot find %PY%
  echo AIFCS is not installed yet. Run scripts\update.bat first.
  echo.
  if defined MSYSTEM exit /b 1
  pause
  exit /b 1
)

if "%~1"=="" (
  echo.
  echo Name the host CSV, in quotes.
  echo.
  if defined MSYSTEM exit /b 2
  pause
  exit /b 2
)

"%PY%" backend\competition\hostcsv.py %*
set EXITCODE=%ERRORLEVEL%

echo.
if defined MSYSTEM exit /b %EXITCODE%
pause
exit /b %EXITCODE%
