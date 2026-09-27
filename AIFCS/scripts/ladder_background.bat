@echo off
REM AIFCS: run the whole queue in the background and give the terminal back.
REM
REM ladder.bat runs in the foreground, so closing that window kills the
REM queue. This one does not: the work moves to its own minimised window
REM that survives closing this terminal and signing out.
REM
REM What it cannot survive is the machine being off or asleep. Nothing can.
REM
REM   ladder_background.bat                    run plans\ladder.yaml
REM   ladder_background.bat plans\other.yaml   run another plan
REM
REM   scripts\train_status.bat                 see how it is going
REM   scripts\train_stop.bat --name v6         stop the step that is running
REM
REM ASCII only. CMD batch and UTF-8 do not mix.
setlocal
cd /d "%~dp0.."

set "PLAN=%~1"
if "%PLAN%"=="" set "PLAN=plans\ladder.yaml"
if not exist "%PLAN%" (
  echo.
  echo No plan at %PLAN%
  echo.

  REM Pause only when double-clicked. Git Bash sets MSYSTEM, so its absence
  REM means Explorer launched this and the window would vanish unread.
  if defined MSYSTEM exit /b 1
  pause
  exit /b 1
)

set "LOGDIR=models\competition\_logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
for /f "tokens=1-4 delims=/ " %%a in ("%DATE%") do set "STAMP=%%d%%b%%c"
set "STAMP=%STAMP%-%TIME:~0,2%%TIME:~3,2%"
set "STAMP=%STAMP: =0%"
set "LOG=%LOGDIR%\ladder-%STAMP%.log"

echo.
echo Queue started in the background.
echo   plan:   %PLAN%
echo   log:    %LOG%
echo   watch:  scripts\train_status.bat
echo   stop:   scripts\train_stop.bat --name ^<step^>
echo.
echo This terminal can be closed. So can your session - sign out is fine.
echo The machine itself has to stay awake: a sleeping laptop computes nothing.
echo.

start "AIFCS ladder" /min cmd /c ""%~dp0ladder.bat" "%PLAN%" > "%LOG%" 2>&1"
exit /b 0
