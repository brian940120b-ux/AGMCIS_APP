<#
    AIFCS emergency stop / 強制關閉

        Double-click scripts\stop.bat

    start.ps1 cleans up after itself when its window closes. This is the safety
    net for when it did not — a crash, a force-closed window, or a leftover
    server from an earlier run holding port 8000 or 5173.
#>

[CmdletBinding()]
param(
    [int[]] $Ports = @(8000, 5173)
)

$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

Write-Host '==> Stopping anything left on the AIFCS ports / 關閉佔用連接埠的程式…'

$stopped = 0
foreach ($port in $Ports) {
    $owners = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue |
              Select-Object -ExpandProperty OwningProcess -Unique
    if (-not $owners) {
        Write-Host "    Port $port : free / 沒有被占用"
        continue
    }
    foreach ($processId in $owners) {
        $name = (Get-Process -Id $processId -ErrorAction SilentlyContinue).ProcessName
        if (-not $name) { $name = 'unknown' }
        Write-Host "    Port $port : stopping $name (PID $processId)"
        & taskkill.exe /PID $processId /T /F 2>&1 | Out-Null
        $stopped++
    }
}

Write-Host ''
if ($stopped -gt 0) {
    Write-Host "Stopped $stopped process(es). / 已關閉 $stopped 個程式。"
} else {
    Write-Host 'Nothing was running. / 沒有需要關閉的程式。'
}
Write-Host ''
Write-Host 'Press Enter to close this window. / 按 Enter 關閉視窗。'
[void](Read-Host)
