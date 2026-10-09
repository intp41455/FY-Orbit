# Find Yourself Desktop Prototype Installer (install.ps1)
[CmdletBinding()]
param (
    [string]$InstallDir = "$env:LOCALAPPDATA\FindYourself",
    [string]$SourceDir = "",
    [switch]$NoShortcut,
    [switch]$AutoStart
)

$ErrorActionPreference = "Stop"

if (-not $SourceDir) {
    $SourceDir = $PSScriptRoot
}

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " [Find Yourself Desktop] Installing Desktop Prototype..." -ForegroundColor Cyan
Write-Host " Target Directory: $InstallDir" -ForegroundColor Gray
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Stop active service if running
$AppDir = Join-Path $InstallDir "app"
$StopScript = Join-Path $AppDir "stop.ps1"
if (Test-Path $StopScript) {
    Write-Host "[1/6] Stopping existing service..." -ForegroundColor Gray
    & powershell -NoProfile -ExecutionPolicy Bypass -File $StopScript
}

# 2. Create directory hierarchy
Write-Host "[2/6] Creating application directories..." -ForegroundColor Gray
$DataDir = Join-Path $InstallDir "data"
$RunDir = Join-Path $InstallDir "run"

@($InstallDir, $AppDir, $DataDir, $RunDir) | ForEach-Object {
    if (-not (Test-Path $_)) {
        New-Item -ItemType Directory -Path $_ -Force | Out-Null
    }
}

# 3. Deploy app files
Write-Host "[3/6] Deploying application and static files..." -ForegroundColor Gray

$srcAppDir = $null
if (Test-Path (Join-Path $SourceDir "app")) {
    $srcAppDir = Join-Path $SourceDir "app"
} elseif (Test-Path (Join-Path $SourceDir "..\app")) {
    $srcAppDir = (Resolve-Path (Join-Path $SourceDir "..\app")).Path
}

if ($srcAppDir -and (Test-Path $srcAppDir)) {
    Copy-Item -Path "$srcAppDir\*" -Destination $AppDir -Recurse -Force
}

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$srcCodeDir = Join-Path $projectRoot "src"
if ((Test-Path $srcCodeDir) -and (-not (Test-Path (Join-Path $AppDir "src")))) {
    Copy-Item -Path $srcCodeDir -Destination $AppDir -Recurse -Force
}

$distDir = Join-Path $projectRoot "web\dist"
$targetWebDir = Join-Path $AppDir "web"
if ((Test-Path $distDir) -and (-not (Test-Path $targetWebDir))) {
    New-Item -ItemType Directory -Path $targetWebDir -Force | Out-Null
    Copy-Item -Path "$distDir\*" -Destination $targetWebDir -Recurse -Force
}

$venvDir = Join-Path $projectRoot ".venv"
$targetVenv = Join-Path $AppDir ".venv"
if ((Test-Path $venvDir) -and (-not (Test-Path $targetVenv))) {
    try {
        New-Item -ItemType Junction -Path $targetVenv -Target $venvDir -ErrorAction SilentlyContinue | Out-Null
    } catch {
        # Junction fallback
    }
}

# 4. Initialize first-time configuration
Write-Host "[4/6] Initializing configuration and credentials..." -ForegroundColor Gray
$ConfigFile = Join-Path $InstallDir "config.env"
if (-not (Test-Path $ConfigFile)) {
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $generatedSecret = -join ($bytes | ForEach-Object { "{0:x2}" -f $_ })
    $generatedToken = "fy-" + (-join ((65..90) + (97..122) + (48..57) | Get-Random -Count 16 | ForEach-Object { [char]$_ }))

    $configLines = @(
        "# Find Yourself Desktop Local Configuration",
        "FY_ENVIRONMENT=local",
        "FY_SESSION_SECRET=$generatedSecret",
        "FY_LOCAL_TOKEN=$generatedToken"
    )
    [System.IO.File]::WriteAllLines($ConfigFile, $configLines, [System.Text.Encoding]::UTF8)
    Write-Host "  -> Generated fresh session key and local credentials" -ForegroundColor Green
} else {
    Write-Host "  -> Existing config.env found, preserving configuration" -ForegroundColor Yellow
}

Copy-Item -Path $ConfigFile -Destination (Join-Path $AppDir "config.env") -Force

# 5. Create Desktop and Start Menu Shortcuts
if (-not $NoShortcut) {
    Write-Host "[5/6] Creating Desktop and Start Menu shortcuts..." -ForegroundColor Gray
    $WshShell = New-Object -ComObject WScript.Shell
    $batPath = Join-Path $AppDir "FindYourself.bat"
    
    $desktopPath = [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::Desktop)
    $shortcutDesktop = $WshShell.CreateShortcut((Join-Path $desktopPath "Find Yourself.lnk"))
    $shortcutDesktop.TargetPath = $batPath
    $shortcutDesktop.WorkingDirectory = $AppDir
    $shortcutDesktop.Description = "Find Yourself Desktop"
    $shortcutDesktop.Save()

    $programsPath = [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::Programs)
    $startMenuDir = Join-Path $programsPath "Find Yourself"
    if (-not (Test-Path $startMenuDir)) { New-Item -ItemType Directory -Path $startMenuDir -Force | Out-Null }
    $shortcutStartMenu = $WshShell.CreateShortcut((Join-Path $startMenuDir "Find Yourself.lnk"))
    $shortcutStartMenu.TargetPath = $batPath
    $shortcutStartMenu.WorkingDirectory = $AppDir
    $shortcutStartMenu.Description = "Find Yourself Desktop"
    $shortcutStartMenu.Save()

    Write-Host "  -> Shortcuts created successfully" -ForegroundColor Green
}

# 6. Copy Uninstaller
Write-Host "[6/6] Copying uninstaller..." -ForegroundColor Gray
$uninstallSrc = Join-Path $PSScriptRoot "uninstall.ps1"
if (Test-Path $uninstallSrc) {
    Copy-Item -Path $uninstallSrc -Destination (Join-Path $InstallDir "uninstall.ps1") -Force
}

Write-Host "==========================================================" -ForegroundColor Green
Write-Host " [Install OK] Find Yourself Desktop Prototype deployed!" -ForegroundColor Green
Write-Host " Install Location: $InstallDir" -ForegroundColor White
Write-Host " Data Directory:   $DataDir" -ForegroundColor White
Write-Host " Launcher:         $AppDir\FindYourself.bat" -ForegroundColor White
Write-Host "==========================================================" -ForegroundColor Green

if ($AutoStart) {
    Write-Host "Starting application..." -ForegroundColor Cyan
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $AppDir "start.ps1")
}
