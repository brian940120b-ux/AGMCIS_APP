<#
    AIFCS one-command launcher for Windows / Windows 一鍵啟動

        Double-click scripts\start.bat

    Does the same job as scripts/start.sh, but natively — no Git Bash needed.
    Checks prerequisites, installs anything missing on the first run, starts the
    backend and the dashboard, and stops both on Ctrl+C.
#>

[CmdletBinding()]
param(
    [int] $BackendPort  = $(if ($env:AIFCS_BACKEND_PORT)  { [int]$env:AIFCS_BACKEND_PORT }  else { 8080 }),
    [int] $FrontendPort = $(if ($env:AIFCS_FRONTEND_PORT) { [int]$env:AIFCS_FRONTEND_PORT } else { 5173 }),
    # Long enough for a first start on a laptop, where loading PyTorch and
    # bundling the dashboard's packages each take minutes rather than seconds.
    # Overridable, because a machine that is simply broken should not make
    # someone sit through three minutes twice.
    [int] $BackendTimeoutSeconds = $(
        if ($env:AIFCS_BACKEND_TIMEOUT_S) { [int]$env:AIFCS_BACKEND_TIMEOUT_S } else { 180 }),
    [int] $FrontendTimeoutSeconds = $(
        if ($env:AIFCS_FRONTEND_TIMEOUT_S) { [int]$env:AIFCS_FRONTEND_TIMEOUT_S } else { 180 }),
    # How the dashboard is served.
    #
    #   built  the backend serves frontend\dist. One process, no Node at run
    #          time, and none of Vite's dependency pre-bundling - which is what
    #          failed on a laptop that ran out of Windows file handles doing it.
    #   dev    Vite's dev server on its own port, with hot reload. For working
    #          on the frontend, which is not what most starts are for.
    [ValidateSet('built', 'dev')]
    [string] $Dashboard = $(if ($env:AIFCS_DASHBOARD) { $env:AIFCS_DASHBOARD } else { 'built' })
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

function Wait-ForAnyHttp {
    <#
        The first of several URLs to answer, or $null. They are tried in turn on
        every pass rather than one being exhausted before the next is reached:
        "localhost" and "127.0.0.1" name the same machine but not always the
        same address, and waiting a full minute on the wrong one before trying
        the right one turns a working dashboard into a two-minute failure.
    #>
    param(
        [string[]] $Urls,
        [int] $TimeoutSeconds,
        [System.Diagnostics.Process] $Process,
        [string[]] $SlowNote = @()
    )
    $started = Get-Date
    $deadline = $started.AddSeconds($TimeoutSeconds)
    $noted = $false
    $nextTick = 50
    while ((Get-Date) -lt $deadline) {
        if ($Process -and $Process.HasExited) { return $null }
        foreach ($url in $Urls) {
            if (Wait-ForHttp $url 0 $Process) { return $url }
        }
        $elapsed = [int]((Get-Date) - $started).TotalSeconds
        if (-not $noted -and $elapsed -ge 20) {
            foreach ($line in $SlowNote) { Write-Host "    $line" }
            $noted = $true
        } elseif ($noted -and $elapsed -ge $nextTick -and $elapsed -lt $TimeoutSeconds) {
            Write-Host "    ...still waiting (${elapsed}s of ${TimeoutSeconds}s)"
            $nextTick = $elapsed + 30
        }
        Start-Sleep -Milliseconds 500
    }
    return $null
}

function Wait-ForHttp {
    param([string] $Url, [int] $TimeoutSeconds, [System.Diagnostics.Process] $Process)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    # do/while, so a timeout of zero is one attempt rather than none — which is
    # what Wait-ForAnyHttp wants when it is running the waiting itself.
    do {
        if ($Process -and $Process.HasExited) { return $false }
        try {
            $null = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
            return $true
        } catch {
            # A 4xx still proves something is answering on the port; a refused
            # connection carries no response and means "not up yet".
            if ($_.Exception.Response) { return $true }
        }
        if ((Get-Date) -ge $deadline) { break }
        Start-Sleep -Milliseconds 500
    } while ($true)
    return $false
}

function Show-Log {
    <#
        Always says something. Returning quietly when the log is missing or
        empty is how a failure reached someone with no evidence attached at
        all — and "the dashboard wrote nothing in three minutes" is itself the
        most useful line in that report, not an absence of one.
    #>
    param([string] $Path, [string] $Label)
    Write-Host ''
    Write-Host "--- $Label ---" -ForegroundColor Yellow
    if (-not (Test-Path $Path)) {
        Write-Host "    (no file at $Path - the process never wrote anything)"
        return
    }
    $tail = Get-Content $Path -Tail 20 -ErrorAction SilentlyContinue
    if (-not $tail) {
        Write-Host "    (the file is empty - the process started but printed nothing)"
        return
    }
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
    $distIndex = Join-Path $Root 'frontend\dist\index.html'
    # Node builds the dashboard; it does not serve it. A machine with a built
    # dashboard and no Node can still run the platform, so the requirement
    # follows what actually has to happen.
    if ($Dashboard -eq 'dev' -or -not (Test-Path $distIndex)) {
        if (-not (Get-Command node -ErrorAction SilentlyContinue)) { $missing += 'Node.js' }
        if (-not (Get-Command npm  -ErrorAction SilentlyContinue)) { $missing += 'npm' }
    }

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
    if (Get-Command node -ErrorAction SilentlyContinue) {
        Write-Host "    Node    $(& node --version)"
    } else {
        Write-Host '    Node    not installed - serving the dashboard that is already built'
    }

    # --- 2. Ports -----------------------------------------------------------
    if (Test-PortBusy $BackendPort) {
        Fail "Port $BackendPort is already in use." "連接埠 $BackendPort 已被占用。先執行 scripts\stop.bat。"
    }
    if ($Dashboard -eq 'dev' -and (Test-PortBusy $FrontendPort)) {
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

    $needsNode = ($Dashboard -eq 'dev') -or -not (Test-Path $distIndex)
    if ($needsNode -and -not (Test-Path (Join-Path $Root 'frontend\node_modules'))) {
        Write-Host '==> First run: installing dashboard dependencies / 首次執行，安裝前端套件…'
        Push-Location (Join-Path $Root 'frontend')
        try {
            & npm install --no-fund --no-audit
            if ($LASTEXITCODE -ne 0) { Fail 'Dashboard dependency install failed.' '前端套件安裝失敗。' }
        } finally { Pop-Location }
    }

    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

    # --- 3b. Dashboard bundle -----------------------------------------------
    # Before the backend, because the backend decides at startup whether there
    # is a bundle to serve.
    if ($Dashboard -eq 'built') {
        $reason = $null
        if (-not (Test-Path $distIndex)) {
            $reason = 'no bundle yet'
        } else {
            $builtAt = (Get-Item $distIndex).LastWriteTimeUtc
            $sources = @('frontend\src', 'frontend\index.html',
                         'frontend\package.json', 'frontend\vite.config.ts')
            foreach ($relative in $sources) {
                $source = Join-Path $Root $relative
                if (-not (Test-Path $source)) { continue }
                $newest = Get-ChildItem $source -Recurse -File -ErrorAction SilentlyContinue |
                    Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
                if ($null -eq $newest) { $newest = Get-Item $source }
                if ($newest.LastWriteTimeUtc -gt $builtAt) {
                    $reason = 'the dashboard changed since it was last built'
                    break
                }
            }
        }

        if ($reason) {
            Write-Host ''
            Write-Host "==> Building the dashboard / 打包前端（$reason）…"
            Write-Host '    This happens once. Later starts reuse it. 只有這次要等，之後會直接用。'
            Push-Location (Join-Path $Root 'frontend')
            try {
                & npm run build
                if ($LASTEXITCODE -ne 0) {
                    Fail 'Dashboard build failed - the lines above say why.' '前端打包失敗，原因在上面。'
                }
            } finally { Pop-Location }
        } else {
            Write-Host '    Dashboard bundle is up to date / 前端已是最新，不用重新打包'
        }
    }

    # --- 4. Backend ---------------------------------------------------------
    # -NoNewWindow keeps both servers attached to this console, so Ctrl+C and
    # closing the window reach them instead of orphaning them in the background.
    Write-Host ''
    Write-Host '==> Starting simulation backend / 啟動模擬引擎…'
    # In dev mode the dev server is the dashboard, so the backend does not also
    # serve whatever bundle happens to be on disk: two dashboards, one of them
    # stale, is a confusing thing to debug.
    if ($Dashboard -eq 'dev') { $env:AIFCS_SERVE_DASHBOARD = '0' }
    $backend = Start-Process -FilePath $venvPy `
        -ArgumentList @('-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', "$BackendPort") `
        -WorkingDirectory (Join-Path $Root 'backend') `
        -RedirectStandardOutput $BackendOut -RedirectStandardError $BackendErr `
        -NoNewWindow -PassThru

    # 60 seconds was too short, and what it produced looked like a broken
    # backend rather than a slow one. Startup imports torch to report whether
    # training is available, and a CUDA build's first import — cold page cache,
    # antivirus reading every DLL — runs well past a minute on a laptop.
    $backendReady = Wait-ForAnyHttp @("http://127.0.0.1:$BackendPort/api/health") `
        $BackendTimeoutSeconds $backend `
        @('Still starting - the first run loads PyTorch, which is slow.',
          '第一次啟動要載入 PyTorch，比較久，請等一下。')
    if (-not $backendReady) {
        Show-Log $BackendErr 'backend log'
        Show-Log $BackendOut 'backend output'
        Fail "Backend did not become healthy in ${BackendTimeoutSeconds}s." `
             "後端 ${BackendTimeoutSeconds} 秒內沒有啟動成功，訊息在上面。"
    }
    Write-Host "    Backend ready - http://127.0.0.1:$BackendPort/docs"

    # --- 5. Dashboard -------------------------------------------------------
    if ($Dashboard -eq 'built') {
        # Already being served by the backend, on the same origin as the API -
        # which is why the frontend's relative /api and /ws paths need no proxy.
        $frontendUrl = "http://127.0.0.1:$BackendPort"
    } else {
        Write-Host '==> Starting dashboard / 啟動儀表板…'
        # npm is a .cmd on Windows, so it cannot be launched directly when output is
        # redirected — it has to go through cmd.exe.
        $npmPath = (Get-Command npm).Source
        $npmArgs = '/c ""' + $npmPath + '" run dev -- --port ' + $FrontendPort + '"'
        $frontend = Start-Process -FilePath $env:ComSpec `
            -ArgumentList $npmArgs `
            -WorkingDirectory (Join-Path $Root 'frontend') `
            -RedirectStandardOutput $FrontOut -RedirectStandardError $FrontErr `
            -NoNewWindow -PassThru

        # Two names for the same machine, because they are not always the same
        # address: "localhost" resolves to the IPv6 loopback first on some Windows
        # setups, and the dev server may be listening only on IPv4, or the reverse.
        $frontendUrl = Wait-ForAnyHttp `
            @("http://127.0.0.1:$FrontendPort", "http://localhost:$FrontendPort") `
            $FrontendTimeoutSeconds $frontend `
            @("Still starting - the first run bundles the dashboard's packages.",
              '第一次啟動要打包前端套件，比較久，請等一下。')
        if (-not $frontendUrl) {
            # A timeout saying only "it did not start", next to a log saying the
            # server is ready, gives the reader nothing to act on. Say what was
            # tried and what came back.
            Write-Host ''
            Write-Host '    The dashboard did not answer. What was tried:'
            foreach ($candidate in @("http://127.0.0.1:$FrontendPort", "http://localhost:$FrontendPort")) {
                try {
                    $response = Invoke-WebRequest -Uri $candidate -UseBasicParsing -TimeoutSec 2
                    Write-Host "      $candidate  ->  HTTP $($response.StatusCode)"
                } catch {
                    Write-Host "      $candidate  ->  $($_.Exception.Message)"
                }
            }
            Write-Host "    Listening on ${FrontendPort}:"
            netstat -an | Select-String ":$FrontendPort\s" | Select-Object -First 5 |
                ForEach-Object { Write-Host "      $_" }
            Write-Host ''
            Show-Log $FrontErr 'dashboard log'
            Show-Log $FrontOut 'dashboard output'
            Write-Host ''
            Write-Host '    The dev server is not the only way to run the dashboard.'
            Write-Host '    Try without it:  .\scripts\start.ps1 -Dashboard built'
            Write-Host '    不用開發伺服器也能跑，上面那行試試看。'
            Fail "Dashboard did not answer in ${FrontendTimeoutSeconds}s." `
                 "前端 ${FrontendTimeoutSeconds} 秒內沒有回應，訊息在上面。"
        }
    }

    # --- 6. Ready -----------------------------------------------------------
    # Set above: the backend's own address in built mode, or whichever of the
    # two names the dev server answered on.
    $url = $frontendUrl
    Write-Host ''
    Write-Host '================================================================' -ForegroundColor Green
    Write-Host '  AIFCS is running.  AIFCS 已啟動。'
    Write-Host ''
    Write-Host '  Open this in your browser / 用瀏覽器打開：'
    Write-Host "      $url" -ForegroundColor Cyan
    Write-Host ''
    Write-Host '  KEEP THIS WINDOW OPEN while you use AIFCS.' -ForegroundColor Yellow
    Write-Host '  使用期間請保持這個視窗開著 — 關掉視窗 AIFCS 就會停止。' -ForegroundColor Yellow
    Write-Host ''
    Write-Host '  To stop: press Ctrl+C here.  要關閉：在這個視窗按 Ctrl+C。'
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
        # $frontend is $null in built mode: there is no second process to
        # outlive, because the backend is serving the dashboard itself.
        if ($frontend -and $frontend.HasExited) {
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
