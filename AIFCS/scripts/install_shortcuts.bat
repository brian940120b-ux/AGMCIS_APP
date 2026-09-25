@echo off
REM The messages below are UTF-8, and CMD defaults to the system codepage —
REM 950 on a Traditional Chinese machine — so without this they print as
REM mojibake. >nul hides chcp's own report.
chcp 65001 >nul
REM
REM Put two shortcuts on the Desktop. Double-click this once; after that the
REM Desktop icons are all that is needed.
setlocal
cd /d "%~dp0.."
set "ROOT=%CD%"

echo.
echo Creating Desktop shortcuts / 建立桌面捷徑

REM The Desktop is asked for rather than assumed to be %USERPROFILE%\Desktop,
REM because OneDrive redirects it and this machine's may already be redirected.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
 "$desk=[Environment]::GetFolderPath('Desktop');" ^
 "$sh=New-Object -ComObject WScript.Shell;" ^
 "$a=$sh.CreateShortcut((Join-Path $desk 'AIFCS 平台.lnk'));" ^
 "$a.TargetPath='%ROOT%\scripts\start.bat'; $a.WorkingDirectory='%ROOT%';" ^
 "$a.Description='Start the AIFCS platform and dashboard'; $a.Save();" ^
 "$b=$sh.CreateShortcut((Join-Path $desk 'AIFCS 訓練.lnk'));" ^
 "$b.TargetPath='%ROOT%\scripts\train.bat'; $b.WorkingDirectory='%ROOT%';" ^
 "$b.Description='Start or resume competition training'; $b.Save();" ^
 "Write-Host ('  -> ' + $desk)"

if errorlevel 1 (
  echo.
  echo Could not create the shortcuts. 無法建立捷徑。
  pause
  exit /b 1
)

echo.
echo Done. Two icons are on your Desktop:
echo 完成。桌面上有兩個圖示：
echo.
echo   AIFCS 平台    開啟模擬平台和網頁介面
echo   AIFCS 訓練    開始或繼續競賽訓練
echo.
pause
