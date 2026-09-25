@echo off
REM The messages below are UTF-8, and CMD defaults to the system codepage —
REM 950 on a Traditional Chinese machine — so without this they print as
REM mojibake. >nul hides chcp's own report.
chcp 65001 >nul
REM
REM AIFCS competition training. Double-click to start, or to carry on.
REM
REM Resuming is the default and needs no flag: the session on disk knows how
REM many steps it has had, and --timesteps is the total to reach rather than an
REM amount to add. Stopping with Ctrl+C, closing this window, or shutting the
REM machine down costs at most one checkpoint interval.
setlocal
cd /d "%~dp0.."

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo.
  echo Cannot find %PY%
  echo AIFCS is not installed yet. Run scripts\update.bat first.
  echo 還沒安裝。請先執行 scripts\update.bat。
  echo.
  pause
  exit /b 1
)

REM Defaults for an overnight run. Anything typed after the file name wins,
REM because argparse takes the last value it sees:
REM     train.bat --timesteps 20000000 --name run2
set "ARGS=--name run1 --algorithm sac --timesteps 5000000 --workers 8 --device auto"

echo.
echo AIFCS competition training
echo ==========================
echo   Ctrl+C or closing this window stops it. Nothing is lost.
echo   按 Ctrl+C 或關掉視窗就會停止，進度不會遺失。
echo   Run it again to carry on from where it stopped.
echo   再執行一次就會從停的地方繼續。
echo.

"%PY%" backend\competition\train.py %ARGS% %*
set EXITCODE=%ERRORLEVEL%

echo.
if "%EXITCODE%"=="0" (
  echo Done. 完成。
) else if "%EXITCODE%"=="130" (
  echo Stopped by you. Progress is saved. 已停止，進度已儲存。
) else (
  echo Something went wrong - the lines above say what.
  echo 上面有寫哪裡出問題。
)
pause
exit /b %EXITCODE%
