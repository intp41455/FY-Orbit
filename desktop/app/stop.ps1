# Find Yourself Desktop Stopper (stop.ps1)
[CmdletBinding()]
param (
    [int]$Port = 8088,
    [string]$AppRoot = "",
    [string]$RunDir = ""
)

$ErrorActionPreference = "SilentlyContinue"

if (-not $AppRoot) {
    $AppRoot = $PSScriptRoot
}
if (-not $RunDir) {
    $parentDir = Split-Path $AppRoot -Parent
    if (Test-Path (Join-Path $parentDir "run")) {
        $RunDir = Join-Path $parentDir "run"
    } else {
        $RunDir = Join-Path $AppRoot "run"
    }
}

$pidFiles = @(
    (Join-Path $RunDir "findyourself.pid"),
    (Join-Path $AppRoot "run\findyourself.pid")
)

$stopped = $false

foreach ($PidFile in $pidFiles) {
    if (Test-Path $PidFile) {
        $pidStr = Get-Content $PidFile -Raw
        if ($pidStr) {
            $targetPid = [int]$pidStr.Trim()
            $p = Get-Process -Id $targetPid -ErrorAction SilentlyContinue
            if ($p) {
                Write-Host "[FindYourself] Stopping process (PID: $targetPid)..." -ForegroundColor Yellow
                Stop-Process -Id $targetPid -Force -ErrorAction SilentlyContinue
                Start-Sleep -Milliseconds 600
                $stopped = $true
            }
        }
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    }
}

# Release lingering connections on port
$connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($connections) {
    foreach ($conn in $connections) {
        if ($conn.OwningProcess -gt 0) {
            Write-Host "[FindYourself] Releasing lingering port $Port process (PID: $($conn.OwningProcess))..." -ForegroundColor Yellow
            Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
            $stopped = $true
        }
    }
}

if ($stopped) {
    Write-Host "[FindYourself] Service stopped successfully." -ForegroundColor Green
} else {
    Write-Host "[FindYourself] No running service process detected." -ForegroundColor Gray
}
