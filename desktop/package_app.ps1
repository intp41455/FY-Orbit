# Find Yourself Desktop Packaging Script (package_app.ps1)
[CmdletBinding()]
param (
    [string]$OutputDir = "",
    [switch]$BundleVenv
)

$ErrorActionPreference = "Stop"

if (-not $OutputDir) {
    $OutputDir = Join-Path $PSScriptRoot "..\dist-desktop"
}

$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path
$WebDir = Join-Path $ProjectRoot "web"
$DistWebDir = Join-Path $WebDir "dist"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " [Find Yourself Desktop] Building Windows Prototype Setup Package..." -ForegroundColor Cyan
Write-Host " Project Root: $ProjectRoot" -ForegroundColor Gray
Write-Host " Output Dir:   $OutputDir" -ForegroundColor Gray
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Build web static assets
Write-Host "[1/4] Building web static assets (npm run build)..." -ForegroundColor Gray
$npmCmd = if ($IsWindows -or $env:OS -match "Windows") { "npm.cmd" } else { "npm" }
Push-Location $WebDir
try {
    & $npmCmd run build
    if ($LASTEXITCODE -ne 0) {
        throw "npm run build failed with exit code: $LASTEXITCODE"
    }
} finally {
    Pop-Location
}

# 2. Prepare output directory
Write-Host "[2/4] Assembling setup package directory hierarchy..." -ForegroundColor Gray
if (Test-Path $OutputDir) {
    Remove-Item $OutputDir -Recurse -Force
}
$SetupDir = Join-Path $OutputDir "FindYourself-Setup"
New-Item -ItemType Directory -Path $SetupDir -Force | Out-Null

# 3. Copy installer and application components
Write-Host "[3/4] Copying core code, installer and runtime..." -ForegroundColor Gray

# Copy installer
$targetInstaller = Join-Path $SetupDir "installer"
New-Item -ItemType Directory -Path $targetInstaller -Force | Out-Null
Copy-Item "$PSScriptRoot\installer\*" -Destination $targetInstaller -Recurse -Force

# Copy app launcher
$targetApp = Join-Path $SetupDir "app"
New-Item -ItemType Directory -Path $targetApp -Force | Out-Null
Copy-Item "$PSScriptRoot\app\*" -Destination $targetApp -Recurse -Force

# Copy Python core source
$targetSrc = Join-Path $targetApp "src"
Copy-Item (Join-Path $ProjectRoot "src") -Destination $targetSrc -Recurse -Force

# Copy web dist
$targetWeb = Join-Path $targetApp "web"
New-Item -ItemType Directory -Path $targetWeb -Force | Out-Null
Copy-Item "$DistWebDir\*" -Destination $targetWeb -Recurse -Force

# Copy venv if specified
$venvDir = Join-Path $ProjectRoot ".venv"
if ($BundleVenv -and (Test-Path $venvDir)) {
    Write-Host "  -> Bundling Python local environment (.venv)..." -ForegroundColor Gray
    Copy-Item $venvDir -Destination (Join-Path $targetApp ".venv") -Recurse -Force
}

# Create top-level Install.bat
$installBat = Join-Path $SetupDir "Install.bat"
$installBatContent = @"
@echo off
title Find Yourself Setup
cd /d "%~dp0"
echo Installing Find Yourself Desktop Prototype...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\install.ps1" -SourceDir "%~dp0"
if %ERRORLEVEL% EQU 0 (
    echo.
    echo Installation finished successfully! Desktop shortcut created.
) else (
    echo.
    echo Installation encountered an error.
)
pause
"@
[System.IO.File]::WriteAllText($installBat, $installBatContent, [System.Text.Encoding]::ASCII)

# Copy README
$readmePath = Join-Path $SetupDir "README.txt"
$readmeLines = @(
    "Find Yourself Desktop (Windows Prototype Preview)",
    "=================================================",
    "Notice: This is a stage testing prototype (PARTIAL state), not a production release.",
    "",
    "Installation:",
    "1. Double click 'Install.bat' to install;",
    "2. Application installs to %LOCALAPPDATA%\FindYourself;",
    "3. Desktop and Start Menu shortcuts will be created;",
    "4. User database is stored in %LOCALAPPDATA%\FindYourself\data;",
    "5. Uninstallation preserves user data by default.",
    "",
    "Manual Control:",
    "- Start:  Double click desktop shortcut or app\FindYourself.bat",
    "- Stop:   Run powershell app\stop.ps1",
    "- Remove: Run powershell %LOCALAPPDATA%\FindYourself\uninstall.ps1"
)
[System.IO.File]::WriteAllLines($readmePath, $readmeLines, [System.Text.Encoding]::ASCII)

# 4. Create ZIP distribution
Write-Host "[4/4] Creating distribution archive (FindYourself-Windows-Setup-Preview.zip)..." -ForegroundColor Gray
$zipFile = Join-Path $OutputDir "FindYourself-Windows-Setup-Preview.zip"
Compress-Archive -Path "$SetupDir\*" -DestinationPath $zipFile -Force

$zipItem = Get-Item $zipFile
Write-Host "==========================================================" -ForegroundColor Green
Write-Host " [Build OK] Desktop prototype setup package created!" -ForegroundColor Green
Write-Host " Setup Directory: $SetupDir" -ForegroundColor White
Write-Host " Archive Path:    $zipFile ($([math]::Round($zipItem.Length / 1MB, 2)) MB)" -ForegroundColor White
Write-Host "==========================================================" -ForegroundColor Green
