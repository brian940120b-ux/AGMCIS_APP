@echo off
REM AIFCS: survive a reboot. Registers a Windows logon task that carries
REM on with any session that still has steps left.
REM
REM Logon, not startup: a startup task would have to run as SYSTEM or hold
REM your password, and neither is worth it to save the minutes between a
REM three-in-the-morning restart and signing in.
REM
REM Run once. Undo with:  scripts\train_autoresume.bat /remove
REM
REM ASCII only. CMD batch and UTF-8 do not mix.
setlocal
cd /d "%~dp0.."

set "TASK=AIFCS training auto-resume"

if /i "%~1"=="/remove" (
  schtasks /delete /tn "%TASK%" /f
  echo.
  echo Auto-resume removed.
  echo.
  pause
  exit /b 0
)

set "TARGET=%~dp0resume_unfinished.bat"
schtasks /create /tn "%TASK%" /tr "\"%TARGET%\"" /sc onlogon /f /rl limited
if errorlevel 1 (
  echo.
  echo Could not register the task. Try running this file as administrator.
  echo.
  pause
  exit /b 1
)

echo.
echo Auto-resume is on.
echo.
echo   After any restart, signing in carries on with the session that
echo   still has steps left. Nothing happens if there is none.
echo.
echo   Check it:  scripts\train_status.bat
echo   Turn off:  scripts\train_autoresume.bat /remove
echo.
pause
