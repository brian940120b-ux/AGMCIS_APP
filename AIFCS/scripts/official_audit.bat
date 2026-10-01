@echo off
REM Read-only extraction of the organiser's folder into official_audit\
REM (git-ignored). Pass the folder path in quotes:
REM   scripts\official_audit.bat "%USERPROFILE%\Desktop\<folder>"
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
"%PY%" backend\competition\official_audit.py %* --out "official_audit"
set EXITCODE=%ERRORLEVEL%

if not "%EXITCODE%"=="0" (
  echo.
  echo Something went wrong. The lines above say what.
  if defined MSYSTEM exit /b %EXITCODE%
  pause
)
exit /b %EXITCODE%
