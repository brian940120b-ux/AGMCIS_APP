@echo off
REM AIFCS emergency stop for Windows.
REM Use this if AIFCS did not shut down cleanly and a port is still in use.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1" %*
