# Put two shortcuts on the Desktop, one for the platform and one for training.
#
# The work is here rather than in the .bat because CMD's batch parser and UTF-8
# do not mix: `chcp 65001` fixes output but not the parsing of the file itself,
# and a line of Chinese was taken as a command to run. PowerShell reads UTF-8
# properly, and start.bat already uses this shape for the same reason.

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

# Asked for rather than assumed to be $env:USERPROFILE\Desktop: OneDrive
# redirects it, and this machine's nearly was.
$desktop = [Environment]::GetFolderPath('Desktop')
Write-Host ""
Write-Host "Creating Desktop shortcuts / 建立桌面捷徑"
Write-Host "  -> $desktop"

$shell = New-Object -ComObject WScript.Shell

$platform = $shell.CreateShortcut((Join-Path $desktop 'AIFCS 平台.lnk'))
$platform.TargetPath = Join-Path $root 'scripts\start.bat'
$platform.WorkingDirectory = $root
$platform.Description = 'Start the AIFCS platform and dashboard'
$platform.Save()

$training = $shell.CreateShortcut((Join-Path $desktop 'AIFCS 訓練.lnk'))
$training.TargetPath = Join-Path $root 'scripts\train.bat'
$training.WorkingDirectory = $root
$training.Description = 'Start or resume competition training'
$training.Save()

Write-Host ""
Write-Host "Done. 完成。桌面上有兩個圖示："
Write-Host "  AIFCS 平台    開啟模擬平台和網頁介面"
Write-Host "  AIFCS 訓練    開始或繼續競賽訓練"
Write-Host ""
