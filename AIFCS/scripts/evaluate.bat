@echo off
REM Score saved sessions with the organiser's own rules. Double-click, or pass
REM session directories:
REM
REM   scripts\evaluate.bat models\competition\run1 models\competition\v2
REM
REM With no arguments it scores every session under models\competition.
REM
REM ASCII only. CMD's batch parser and UTF-8 do not mix, and a line of Chinese
REM in a .bat here was taken as a command to run. evaluate.py prints in both
REM languages, because Python writes to the Windows console through an API
REM that ignores the codepage.
setlocal enabledelayedexpansion
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

set "ARGS=%*"
if "%ARGS%"=="" (
  for /d %%D in (models\competition\*) do (
    if exist "%%D\checkpoint.zip" set "ARGS=!ARGS! %%D"
  )
)

if "%ARGS%"=="" (
  echo.
  echo No trained sessions found under models\competition.
  echo Run scripts\train.bat first.
  echo.
  pause
  exit /b 1
)

"%PY%" backend\competition\evaluate.py %ARGS%
set EXITCODE=%ERRORLEVEL%

echo.
pause
exit /b %EXITCODE%
