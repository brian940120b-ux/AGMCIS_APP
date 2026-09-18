<#
    AIFCS one-command launcher for Windows / Windows 一鍵啟動

        Double-click scripts\start.bat

    Does the same job as scripts/start.sh, but natively — no Git Bash needed.
    Checks prerequisites, installs anything missing on the first run, starts the
    backend and the dashboard, and stops both on Ctrl+C.
#>

[CmdletBinding()]
param(
    [int] $BackendPort  = $(if ($env:AIFCS_BACKEND_PORT)  { [int]$env:AIFCS_BACKEND_PORT }  else { 8000 }),
    [int] $FrontendPort = $(if ($env:AIFCS_FRONTEND_PORT) { [int]$env:AIFCS_FRONTEND_PORT } else { 5173 })
)

$ErrorActionPreference = 'Stop'

# Bilingual output only survives if the console agrees it is UTF-8.
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$Root       = Split-Path -Parent $PSScriptRoot
$LogDir     = Join-Path $Root 'data\telemetry'
$BackendOut = Join-Path $LogDir 'backend.out'
$BackendErr = Join-Path $LogDir 'backend.err'
$FrontOut   = Join-Path $LogDir 'frontend.out'
$FrontErr   = Join-Path $LogDir 'frontend.err'

$backend  = $null
$frontend = $null

# start.bat pauses on a non-zero exit, so the message stays readable.
function Fail {
    param([string] $English, [string] $Chinese)
    Write-Host ''
    Write-Host "!!  $English" -ForegroundColor Red
    Write-Host "    $Chinese"  -ForegroundColor Red
    Write-Host ''
    exit 1
}

# Windows ships a "python" stub that opens the Microsoft Store and installs
# nothing, so the version check — not the presence of the command — decides.
# Returns a hashtable: Exe is the launcher, Pre the arguments it needs first.
function Find-Python {
    $candidates = @(
        @{ Exe = 'py';      Pre = @('-3') },
        @{ Exe = 'python';  Pre = @() },
        @{ Exe = 'python3'; Pre = @() }
    )
    foreach ($candidate in $candidates) {
        if (-not (Get-Command $candidate.Exe -ErrorAction SilentlyContinue)) { continue }
        $exe   = $candidate.Exe
        $probe = @($candidate.Pre) + @('-c', 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)')
        try { & $exe @probe 2>$null } catch { continue }
        if ($LASTEXITCODE -eq 0) { return $candidate }
    }
    return $null
}

function Test-PortBusy {
    param([int] $Port)
    $listening = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    return [bool]$listening
}

function Wait-ForHttp {
    param([string] $Url, [int] $TimeoutSeconds, [System.Diagnostics.Process] $Process)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if ($Process -and $Process.HasExited) { return $false }
        try {
            $null = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
            return $true
        } catch {
            # A 4xx still proves something is answering on the port; a refused
            # connection carries no response and means "not up yet".
            if ($_.Exception.Response) { return $true }
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Show-Log {
    param([string] $Path, [string] $Label)
    if (-not (Test-Path $Path)) { return }
    $tail = Get-Content $Path -Tail 20 -ErrorAction SilentlyContinue
    if (-not $tail) { return }
    Write-Host ''
    Write-Host "--- $Label ---" -ForegroundColor Yellow
    $tail | ForEach-Object { Write-Host $_ }
}

# npm run dev spawns Vite as a child; killing only the parent leaves Vite
# holding the port. /T takes the whole tree, which is what `set -m` buys the
# shell script on macOS and Linux.
function Stop-Tree {
    param([System.Diagnostics.Process] $Process)
    if ($null -eq $Process) { return }
    try { if ($Process.HasExited) { return } } catch { return }
    & taskkill.exe /PID $Process.Id /T /F 2>&1 | Out-Null
}

try {
    Write-Host '==> AIFCS - AI Flight Command and Simulation Platform' -ForegroundColor Cyan
    Write-Host ''

    # --- 1. Prerequisites ---------------------------------------------------
    $missing = @()
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) { $missing += 'Node.js' }
    if (-not (Get-Command npm  -ErrorAction SilentlyContinue)) { $missing += 'npm' }

    $python = Find-Python
    if ($null -eq $python) { $missing += 'Python 3.11+' }

    if ($missing.Count -gt 0) {
        $list = $missing -join ', '
        Fail "Missing: $list" "缺少這些程式：$list — 安裝後再執行一次。"
    }

    # Splatting needs a plain variable, so unpack the hashtable first.
    $PyExe = $python.Exe
    $PyPre = @($python.Pre)

    Write-Host "    Python  $(& $PyExe @PyPre --version)  ($PyExe)"
    Write-Host "    Node    $(& node --version)"

    # --- 2. Ports -----------------------------------------------------------
    if (Test-PortBusy $BackendPort) {
        Fail "Port $BackendPort is already in use." "連接埠 $BackendPort 已被占用。先執行 scripts\stop.bat。"
    }
    if (Test-PortBusy $FrontendPort) {
        Fail "Port $FrontendPort is already in use." "連接埠 $FrontendPort 已被占用。先執行 scripts\stop.bat。"
    }

    # --- 3. Install anything missing ---------------------------------------
    $venvPy = Join-Path $Root '.venv\Scripts\python.exe'
    if (-not (Test-Path $venvPy)) {
        Write-Host ''
        Write-Host '==> First run: installing backend dependencies / 首次執行，安裝後端套件…'
        Push-Location $Root
        try {
            & $PyExe @PyPre -m venv .venv
            if ($LASTEXITCODE -ne 0) { Fail 'Could not create the virtualenv.' '無法建立虛擬環境。' }
            if (-not (Test-Path $venvPy)) { Fail 'The virtualenv has no interpreter.' '虛擬環境建立失敗。' }
            & $venvPy -m pip install --upgrade pip --quiet
            & $venvPy -m pip install -r requirements-dev.txt --quiet
            if ($LASTEXITCODE -ne 0) { Fail 'Backend dependency install failed.' '後端套件安裝失敗。' }
        } finally { Pop-Location }
    }

    if (-not (Test-Path (Join-Path $Root 'frontend\node_modules'))) {
        Write-Host '==> First run: installing dashboard dependencies / 首次執行，安裝前端套件…'
        Push-Location (Join-Path $Root 'frontend')
        try {
            & npm install --no-fund --no-audit
            if ($LASTEXITCODE -ne 0) { Fail 'Dashboard dependency install failed.' '前端套件安裝失敗。' }
        } finally { Pop-Location }
    }

    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

    # --- 4. Backend ---------------------------------------------------------
    # -NoNewWindow keeps both servers attached to this console, so Ctrl+C and
    # closing the window reach them instead of orphaning them in the background.
    Write-Host ''
    Write-Host '==> Starting simulation backend / 啟動模擬引擎…'
    $backend = Start-Process -FilePath $venvPy `
        -ArgumentList @('-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', "$BackendPort") `
        -WorkingDirectory (Join-Path $Root 'backend') `
        -RedirectStandardOutput $BackendOut -RedirectStandardError $BackendErr `
        -NoNewWindow -PassThru

    if (-not (Wait-ForHttp "http://127.0.0.1:$BackendPort/api/health" 60 $backend)) {
        Show-Log $BackendErr 'backend log'
        Show-Log $BackendOut 'backend output'
        Fail 'Backend did not become healthy in 60s.' '後端 60 秒內沒有啟動成功，訊息在上面。'
    }
    Write-Host "    Backend ready - http://127.0.0.1:$BackendPort/docs"

    # --- 5. Dashboard -------------------------------------------------------
    # npm is a .cmd on Windows, so it cannot be launched directly when output is
    # redirected — it has to go through cmd.exe.
    Write-Host '==> Starting dashboard / 啟動儀表板…'
    $npmPath = (Get-Command npm).Source
    $npmArgs = '/c ""' + $npmPath + '" run dev -- --port ' + $FrontendPort + '"'
    $frontend = Start-Process -FilePath $env:ComSpec `
        -ArgumentList $npmArgs `
        -WorkingDirectory (Join-Path $Root 'frontend') `
        -RedirectStandardOutput $FrontOut -RedirectStandardError $FrontErr `
        -NoNewWindow -PassThru

    if (-not (Wait-ForHttp "http://127.0.0.1:$FrontendPort" 60 $frontend)) {
        Show-Log $FrontErr 'dashboard log'
        Show-Log $FrontOut 'dashboard output'
        Fail 'Dashboard did not start in 60s.' '前端 60 秒內沒有啟動成功，訊息在上面。'
    }

    # --- 6. Ready -----------------------------------------------------------
    $url = "http://localhost:$FrontendPort"
    Write-Host ''
    Write-Host '================================================================' -ForegroundColor Green
    Write-Host '  AIFCS is running.  AIFCS 已啟動。'
    Write-Host ''
    Write-Host '  Open this in your browser / 用瀏覽器打開：'
    Write-Host "      $url" -ForegroundColor Cyan
    Write-Host ''
    Write-Host '  Press Ctrl+C here to stop.  在這個視窗按 Ctrl+C 可以關閉。'
    Write-Host '================================================================' -ForegroundColor Green
    Write-Host ''

    try { Start-Process $url } catch { }

    while ($true) {
        Start-Sleep -Seconds 1
        if ($backend.HasExited) {
            Show-Log $BackendErr 'backend log'
            Write-Host 'Backend stopped. / 後端已停止。' -ForegroundColor Yellow
            break
        }
        if ($frontend.HasExited) {
            Show-Log $FrontErr 'dashboard log'
            Write-Host 'Dashboard stopped. / 前端已停止。' -ForegroundColor Yellow
            break
        }
    }
} finally {
    # Nothing was started (a prerequisite check failed) — stay quiet, so a
    # "Stopping…" message never muddies the error the user needs to read.
    if ($backend -or $frontend) {
        Write-Host ''
        Write-Host '==> Stopping AIFCS / 正在關閉…'
        Stop-Tree $frontend
        Stop-Tree $backend
        Write-Host '    Stopped. / 已關閉。'
    }
}
