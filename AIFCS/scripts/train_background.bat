@echo off
REM AIFCS: start training in the background and give the terminal back.
REM
REM The training window is minimised, not hidden, so it is visible in the
REM taskbar and the machine cannot be quietly left training. Closing THIS
REM window does not stop it; scripts\train_stop.bat does.
REM
REM ASCII only. CMD batch and UTF-8 do not mix.
setlocal
cd /d "%~dp0.."

if "%~1"=="" (
  echo.
  echo Usage: train_background.bat --name v5 [other train.bat flags]
  echo.
  echo   The name is required here, because stopping and checking status
  echo   both need to know which session you meant.
  echo.
  pause
  exit /b 1
)

set "LOGDIR=models\competition\_logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
for /f "tokens=1-4 delims=/ " %%a in ("%DATE%") do set "STAMP=%%d%%b%%c"
set "STAMP=%STAMP%-%TIME:~0,2%%TIME:~3,2%"
set "STAMP=%STAMP: =0%"
set "LOG=%LOGDIR%\train-%STAMP%.log"

echo.
echo Starting training in the background.
echo   log:   %LOG%
echo   watch: scripts\train_status.bat
echo   stop:  scripts\train_stop.bat --name ^<session^>
echo.
echo This window can be closed. The training window cannot - minimise it.
echo.

start "AIFCS training" /min cmd /c ""%~dp0train.bat" %* > "%LOG%" 2>&1"
exit /b 0
