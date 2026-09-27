@echo off
REM AIFCS: ask a background run to stop, cleanly.
REM
REM It stops at its next step and saves on the way out, the same path Ctrl+C
REM takes. Killing the window instead costs up to one checkpoint interval.
setlocal
cd /d "%~dp0.."

set "NAME=%~1"
if /i "%NAME%"=="--name" set "NAME=%~2"
if "%NAME%"=="" (
  echo.
  echo Usage: train_stop.bat --name v5
  echo.
  echo Sessions on disk:
  dir /b models\competition 2>nul
  echo.

  REM Pause only when double-clicked. Git Bash sets MSYSTEM, so its absence
  REM means Explorer launched this and the window would vanish unread. From a
  REM terminal, `pause` eats the first character of whatever is typed next:
  REM three pastes of `scripts\ladder.bat` arrived as `cripts/ladder.bat`.
  if defined MSYSTEM exit /b 1
  pause
  exit /b 1
)

if not exist "models\competition\%NAME%" (
  echo.
  echo No session called %NAME%. Sessions on disk:
  dir /b models\competition 2>nul
  echo.

  REM Pause only when double-clicked. Git Bash sets MSYSTEM, so its absence
  REM means Explorer launched this and the window would vanish unread. From a
  REM terminal, `pause` eats the first character of whatever is typed next:
  REM three pastes of `scripts\ladder.bat` arrived as `cripts/ladder.bat`.
  if defined MSYSTEM exit /b 1
  pause
  exit /b 1
)

echo. > "models\competition\%NAME%\STOP"
echo.
echo Asked %NAME% to stop. It saves and exits within a few seconds.
echo Run scripts\train_status.bat to watch it finish.
echo.

REM Pause only when double-clicked. Git Bash sets MSYSTEM, so its absence
REM means Explorer launched this and the window would vanish unread. From a
REM terminal, `pause` eats the first character of whatever is typed next:
REM three pastes of `scripts\ladder.bat` arrived as `cripts/ladder.bat`.
if defined MSYSTEM exit /b %ERRORLEVEL%
pause
