@echo off
REM Bring this machine up to date and start AIFCS. Double-click this file.
REM 一個指令做完：更新、安裝、檢查、啟動。
REM
REM It needs Git for Windows, which provides the bash that runs update.sh.
setlocal
cd /d "%~dp0.."

where bash >/dev/null 2>&1
if errorlevel 1 (
  echo.
  echo Git for Windows is not installed, or bash is not on PATH.
  echo 找不到 Git for Windows。
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
  echo 上面有寫哪裡出問題。
  pause
)
exit /b %EXITCODE%
