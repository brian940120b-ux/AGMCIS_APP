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
cd backend
"..\%PY%" -m competition.official_audit %* --out "..\official_audit"
set EXITCODE=%ERRORLEVEL%
cd ..

if not "%EXITCODE%"=="0" (
  echo.
  echo Something went wrong. The lines above say what.
  if defined MSYSTEM exit /b %EXITCODE%
  pause
)
exit /b %EXITCODE%
