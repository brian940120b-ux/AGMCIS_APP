@echo off
REM AIFCS: carry on with whatever was still training, after a restart.
REM
REM Registered as a logon task by scripts\train_autoresume.bat. Windows
REM Update restarting at three in the morning used to cost the night; with
REM this it costs the minutes until someone signs in.
REM
REM Silence is the normal case: most logons happen with nothing in flight.
REM
REM ASCII only. CMD batch and UTF-8 do not mix.
setlocal
cd /d "%~dp0.."

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" exit /b 0

set "NAME="
for /f "delims=" %%s in ('"%PY%" -m competition.unfinished 2^>nul') do set "NAME=%%s"
if "%NAME%"=="" exit /b 0

REM Only the name is needed: a resume reads everything else off the card,
REM so the experiment cannot be changed by accident here.
call "%~dp0train_background.bat" --name %NAME%
exit /b 0
