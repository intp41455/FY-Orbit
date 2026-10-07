# Find Yourself Web Deployment Automation Script
[CmdletBinding()]
param (
    [int]$Port = 8000,
    [switch]$BuildOnly,
    [switch]$Docker
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " [FY Orbit · 星轨] Web Deployment Automation" -ForegroundColor Cyan
Write-Host " Root Directory: $Root" -ForegroundColor Gray
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Build Vite PWA frontend
Write-Host "[1/3] Building frontend production bundle (web/dist)..." -ForegroundColor Yellow
Push-Location (Join-Path $Root "web")
try {
    $npmCmd = if ($IsWindows -or $env:OS -match "Windows") { "npm.cmd" } else { "npm" }
    & $npmCmd run build
    if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
} finally {
    Pop-Location
}
Write-Host "    Frontend build completed successfully." -ForegroundColor Green

if ($BuildOnly) {
    Write-Host "==> BuildOnly specified. Done." -ForegroundColor Green
    exit 0
}

if ($Docker) {
    Write-Host "[2/3] Building and running Docker container..." -ForegroundColor Yellow
    docker-compose up -d --build
    Write-Host "==> Docker service is up on http://localhost:$Port" -ForegroundColor Green
    exit 0
}

# 2. Local launch
Write-Host "[2/3] Launching unified FastAPI + Static server on port $Port..." -ForegroundColor Yellow
$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    $fallbackPys = @(
        $env:PYTHON_EXECUTABLE
    )
    foreach ($cand in $fallbackPys) {
        if ($cand -and (Test-Path $cand)) { $py = $cand; break }
    }
}
if (-not (Test-Path $py)) {
    $sysPy = Get-Command python -ErrorAction SilentlyContinue
    if ($sysPy) { $py = $sysPy.Source }
}

& $py (Join-Path $Root "run.py") --port $Port
