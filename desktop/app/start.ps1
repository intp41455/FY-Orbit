# Find Yourself Desktop Launcher (start.ps1)
[CmdletBinding()]
param (
    [int]$Port = 8088,
    [switch]$NoBrowser,
    [string]$AppRoot = "",
    [string]$DataDir = "",
    [string]$RunDir = ""
)

$ErrorActionPreference = "Stop"

if (-not $AppRoot) {
    $AppRoot = $PSScriptRoot
}
if (-not $DataDir) {
    $parentDir = Split-Path $AppRoot -Parent
    if (Test-Path (Join-Path $parentDir "data")) {
        $DataDir = Join-Path $parentDir "data"
    } else {
        $DataDir = "$env:LOCALAPPDATA\FindYourself\data"
    }
}
if (-not $RunDir) {
    $parentDir = Split-Path $AppRoot -Parent
    if (Test-Path (Join-Path $parentDir "run")) {
        $RunDir = Join-Path $parentDir "run"
    } else {
        $RunDir = Join-Path $AppRoot "run"
    }
}

if (-not (Test-Path $RunDir)) { New-Item -ItemType Directory -Path $RunDir -Force | Out-Null }
if (-not (Test-Path $DataDir)) { New-Item -ItemType Directory -Path $DataDir -Force | Out-Null }

$PidFile = Join-Path $RunDir "findyourself.pid"

# Check if already running
if (Test-Path $PidFile) {
    $existingPid = Get-Content $PidFile -ErrorAction SilentlyContinue
    if ($existingPid -and (Get-Process -Id $existingPid -ErrorAction SilentlyContinue)) {
        Write-Host "[FindYourself] Service already running (PID: $existingPid, Port: $Port)" -ForegroundColor Yellow
        if (-not $NoBrowser) {
            Start-Process "http://127.0.0.1:$Port"
        }
        exit 0
    } else {
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    }
}

# Find Python interpreter
$PythonCmd = $null
$possiblePythons = @(
    (Join-Path $AppRoot "runtime\python.exe"),
    (Join-Path $AppRoot ".venv\Scripts\python.exe"),
    (Join-Path (Split-Path $AppRoot -Parent) ".venv\Scripts\python.exe"),
    (Join-Path $AppRoot "..\..\.venv\Scripts\python.exe"),
    (Join-Path $AppRoot "..\.venv\Scripts\python.exe"),
    "python"
)

foreach ($py in $possiblePythons) {
    if (Test-Path $py) {
        $PythonCmd = (Resolve-Path $py).Path
        break
    } elseif ($py -eq "python" -and (Get-Command python -ErrorAction SilentlyContinue)) {
        $PythonCmd = "python"
        break
    }
}

if (-not $PythonCmd) {
    Write-Error "[FindYourself Error] Python runtime not found."
    exit 1
}

# Configure PYTHONPATH to include src
$srcDir = Join-Path $AppRoot "src"
if (Test-Path $srcDir) {
    $existingPyPath = [System.Environment]::GetEnvironmentVariable("PYTHONPATH", "Process")
    if ($existingPyPath) {
        [System.Environment]::SetEnvironmentVariable("PYTHONPATH", "$srcDir;$existingPyPath", "Process")
    } else {
        [System.Environment]::SetEnvironmentVariable("PYTHONPATH", $srcDir, "Process")
    }
}

# Load or configure env
$ConfigFile = Join-Path $AppRoot "config.env"
if (-not (Test-Path $ConfigFile)) {
    $parentConfig = Join-Path (Split-Path $AppRoot -Parent) "config.env"
    if (Test-Path $parentConfig) {
        $ConfigFile = $parentConfig
    }
}
$SessionSecret = "desktop-session-secret-local-32chars-min-key!"
$LocalToken = "desktop-token-secret"

if (Test-Path $ConfigFile) {
    Get-Content $ConfigFile | ForEach-Object {
        if ($_ -match "^\s*([^#=]+)=(.*)$") {
            $k = $matches[1].Trim()
            $v = $matches[2].Trim()
            [System.Environment]::SetEnvironmentVariable($k, $v, "Process")
        }
    }
} else {
    $env:FY_ENVIRONMENT = "local"
    $env:FY_SESSION_SECRET = $SessionSecret
    $env:FY_LOCAL_TOKEN = $LocalToken
}

# Determine web static dir
$StaticDir = Join-Path $AppRoot "web"
if (-not (Test-Path $StaticDir)) {
    $StaticDir = Join-Path $AppRoot "..\web\dist"
}

$DbPath = Join-Path $DataDir "find-yourself.db"

$env:FY_ENVIRONMENT = "local"
$env:FY_DATABASE_URL = "sqlite:///$($DbPath -replace '\\', '/')"
$env:FY_PUBLIC_URL = "http://127.0.0.1:$Port"
if (Test-Path $StaticDir) {
    $env:FY_STATIC_DIR = (Resolve-Path $StaticDir).Path
}

$LogFile = Join-Path $RunDir "app.log"
$ErrLogFile = Join-Path $RunDir "app.err.log"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " [Find Yourself Desktop] Starting Service..." -ForegroundColor Cyan
Write-Host " Port: $Port | Database: $DbPath" -ForegroundColor Gray
Write-Host "==========================================================" -ForegroundColor Cyan

# Start backend process
$proc = Start-Process -FilePath $PythonCmd `
    -ArgumentList "-m uvicorn find_yourself.api.app:create_app --factory --host 127.0.0.1 --port $Port --log-level info" `
    -WorkingDirectory $AppRoot `
    -NoNewWindow `
    -PassThru `
    -RedirectStandardOutput $LogFile `
    -RedirectStandardError $ErrLogFile

$proc.Id | Out-File $PidFile -Encoding ascii

# Wait for live probe
$healthUrl = "http://127.0.0.1:$Port/health/live"
$timeoutSec = 20
$deadline = (Get-Date).AddSeconds($timeoutSec)
$isHealthy = $false

while ((Get-Date) -lt $deadline) {
    if ($proc.HasExited) {
        Write-Error "[FindYourself Error] Process exited unexpectedly with code $($proc.ExitCode). Check log: $LogFile"
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
        exit 1
    }
    try {
        $resp = Invoke-WebRequest -Uri $healthUrl -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
        if ($resp.StatusCode -eq 200) {
            $isHealthy = $true
            break
        }
    } catch {
        Start-Sleep -Milliseconds 400
    }
}

if (-not $isHealthy) {
    Write-Error "[FindYourself Error] Startup timeout: /health/live not reachable in ${timeoutSec}s."
    exit 1
}

Write-Host " [Find Yourself Desktop] Service Ready! (PID: $($proc.Id))" -ForegroundColor Green
Write-Host " URL: http://127.0.0.1:$Port" -ForegroundColor Green

if (-not $NoBrowser) {
    $webviewDataDir = Join-Path $RunDir "webview-profile"
    if (-not (Test-Path $webviewDataDir)) {
        New-Item -ItemType Directory -Path $webviewDataDir -Force | Out-Null
    }

    $edgeExe = $null
    $edgeCandidates = @(
        "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        "$env:LOCALAPPDATA\Microsoft\Edge\Application\msedge.exe"
    )
    foreach ($cand in $edgeCandidates) {
        if (Test-Path $cand) {
            $edgeExe = $cand
            break
        }
    }

    if ($edgeExe) {
        Write-Host " [Find Yourself Desktop] Launching standalone application window (Edge App Mode)..." -ForegroundColor Cyan
        $winProc = Start-Process -FilePath $edgeExe `
            -ArgumentList "--app=http://127.0.0.1:$Port", "--window-size=1280,840", "--user-data-dir=`"$webviewDataDir`"", "--no-first-run", "--no-default-browser-check", "--disable-background-mode" `
            -PassThru
        Write-Host " [Find Yourself Desktop] Standalone window launched (PID: $($winProc.Id))." -ForegroundColor Green
    } else {
        Write-Host " [Find Yourself Desktop] Standalone window engine not detected, opening default browser as fallback..." -ForegroundColor Yellow
        Start-Process "http://127.0.0.1:$Port"
    }
}

