@echo off
REM Put AIFCS shortcuts on the Desktop. Double-click this once.
REM
REM This file is ASCII on purpose. CMD's batch parser and UTF-8 do not mix:
REM chcp 65001 fixes output but not the parsing of the file itself, and a line
REM of Chinese here was taken as a command to run. The Chinese lives in the
REM PowerShell script, which reads UTF-8 properly.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_shortcuts.ps1" %*
if errorlevel 1 (
  echo.
  echo Could not create the shortcuts.

  REM Pause only when double-clicked. Git Bash sets MSYSTEM, so its absence
  REM means Explorer launched this and the window would vanish unread. From a
  REM terminal, `pause` eats the first character of whatever is typed next:
  REM three pastes of `scripts\ladder.bat` arrived as `cripts/ladder.bat`.
  if defined MSYSTEM exit /b 1
  pause
  exit /b 1
)

REM Pause only when double-clicked. Git Bash sets MSYSTEM, so its absence
REM means Explorer launched this and the window would vanish unread. From a
REM terminal, `pause` eats the first character of whatever is typed next:
REM three pastes of `scripts\ladder.bat` arrived as `cripts/ladder.bat`.
if defined MSYSTEM exit /b %ERRORLEVEL%
pause
