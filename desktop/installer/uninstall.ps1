# Find Yourself Desktop Prototype Uninstaller (uninstall.ps1)
[CmdletBinding()]
param (
    [string]$InstallDir = "$env:LOCALAPPDATA\FindYourself",
    [object]$PreserveData = $true,
    [switch]$Force
)

$ErrorActionPreference = "SilentlyContinue"

$shouldPreserve = $true
if ($PreserveData -ne $null) {
    if ($PreserveData -is [bool]) {
        $shouldPreserve = $PreserveData
    } else {
        $strVal = "$PreserveData".Trim().ToLower()
        if ($strVal -eq "false" -or $strVal -eq "0" -or $strVal -eq "no") {
            $shouldPreserve = $false
        } else {
            $shouldPreserve = $true
        }
    }
}

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " [Find Yourself Desktop] Executing Uninstall..." -ForegroundColor Cyan
Write-Host " Target Directory: $InstallDir" -ForegroundColor Gray
Write-Host " Preserve Data:    $shouldPreserve" -ForegroundColor Gray
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Stop service
$AppDir = Join-Path $InstallDir "app"
$StopScript = Join-Path $AppDir "stop.ps1"
if (Test-Path $StopScript) {
    Write-Host "[1/4] Stopping active services..." -ForegroundColor Gray
    & powershell -NoProfile -ExecutionPolicy Bypass -File $StopScript
}

# 2. Remove Shortcuts
Write-Host "[2/4] Removing system shortcuts..." -ForegroundColor Gray
$desktopPath = [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::Desktop)
$desktopShortcut = Join-Path $desktopPath "Find Yourself.lnk"
if (Test-Path $desktopShortcut) { Remove-Item $desktopShortcut -Force }

$programsPath = [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::Programs)
$startMenuDir = Join-Path $programsPath "Find Yourself"
if (Test-Path $startMenuDir) { Remove-Item $startMenuDir -Recurse -Force }

# 3. Clean application and runtime
Write-Host "[3/4] Removing application components..." -ForegroundColor Gray
if (Test-Path $AppDir) { Remove-Item $AppDir -Recurse -Force }
$RunDir = Join-Path $InstallDir "run"
if (Test-Path $RunDir) { Remove-Item $RunDir -Recurse -Force }

# 4. Handle user data
$DataDir = Join-Path $InstallDir "data"
Write-Host "[4/4] User data processing..." -ForegroundColor Gray
if ($shouldPreserve) {
    Write-Host "  -> User database and history preserved at: $DataDir" -ForegroundColor Green
    Write-Host "  -> Local credentials preserved at: $(Join-Path $InstallDir 'config.env')" -ForegroundColor Green
} else {
    Write-Host "  -> Cleaning all user data..." -ForegroundColor Yellow
    if (Test-Path $InstallDir) { Remove-Item $InstallDir -Recurse -Force }
}

Write-Host "==========================================================" -ForegroundColor Green
Write-Host " [Uninstall OK] Find Yourself Desktop uninstallation finished." -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green
