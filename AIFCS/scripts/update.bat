@echo off
REM Bring this machine up to date and start AIFCS. Double-click this file.
REM
REM It needs Git for Windows, which provides the bash that runs update.sh.
REM
REM ASCII only. CMD's batch parser and UTF-8 do not mix: chcp 65001 fixes
REM output but not the parsing of the file itself, and a line of Chinese in a
REM .bat here was taken as a command to run. update.sh prints in both
REM languages, and bash has no such problem.
setlocal
cd /d "%~dp0.."

where bash >nul 2>&1
if errorlevel 1 (
  echo.
  echo Git for Windows is not installed, or bash is not on PATH.
  echo Install it from https://git-scm.com/download/win then run this again.
  echo.
  pause
  exit /b 1
)

bash scripts/update.sh %*
set EXITCODE=%ERRORLEVEL%

if not "%EXITCODE%"=="0" (
  echo.
  echo Something went wrong. The lines above say what.
  pause
)
exit /b %EXITCODE%
