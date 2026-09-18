@echo off
REM AIFCS one-command launcher for Windows.
REM Double-click this file, or run it from CMD / PowerShell.
REM
REM It only hands over to start.ps1, which does the real work. The wrapper
REM exists so that double-clicking works regardless of the machine's
REM PowerShell execution policy.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
if errorlevel 1 pause
