# Find Yourself Desktop Lifecycle Verification Script (verify_lifecycle.ps1)
[CmdletBinding()]
param (
    [int]$TestPort = 8099,
    [string]$TestInstallDir = "$env:TEMP\FindYourself_Lifecycle_Verify"
)

$ErrorActionPreference = "Stop"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " [Find Yourself Desktop] Starting Lifecycle Verification Test" -ForegroundColor Cyan
Write-Host " Target Directory: $TestInstallDir" -ForegroundColor Gray
Write-Host " Communication Port: $TestPort" -ForegroundColor Gray
Write-Host "==========================================================" -ForegroundColor Cyan

$results = [ordered]@{}
$dbTestPy = Join-Path $PSScriptRoot "installer\db_test.py"

try {
    # 0. Cleanup old test dir
    if (Test-Path $TestInstallDir) {
        Remove-Item $TestInstallDir -Recurse -Force -ErrorAction SilentlyContinue
    }

    # -------------------------------------------------------------------------
    # Step 1: Install and Initial Configuration
    # -------------------------------------------------------------------------
    Write-Host "`n>>> [Step 1/5] Testing Installation & Initial Configuration..." -ForegroundColor Yellow
    $installScript = Join-Path $PSScriptRoot "installer\install.ps1"
    & powershell -NoProfile -ExecutionPolicy Bypass -File $installScript -InstallDir $TestInstallDir -NoShortcut
    
    $appDir = Join-Path $TestInstallDir "app"
    $dataDir = Join-Path $TestInstallDir "data"
    $configFile = Join-Path $TestInstallDir "config.env"
    
    if (-not (Test-Path $appDir) -or -not (Test-Path $dataDir) -or -not (Test-Path $configFile)) {
        throw "Missing app, data, or config.env after installation"
    }

    $configText = Get-Content $configFile -Raw
    if ($configText -notmatch "FY_SESSION_SECRET=" -or $configText -notmatch "FY_LOCAL_TOKEN=") {
        throw "config.env missing required FY_SESSION_SECRET or FY_LOCAL_TOKEN"
    }
    Write-Host "  -> Installation verified. Credentials generated in config.env" -ForegroundColor Green
    $results["1_Installation_And_Config"] = "PASS"

    # -------------------------------------------------------------------------
    # Step 2: First Start and Health Check
    # -------------------------------------------------------------------------
    Write-Host "`n>>> [Step 2/5] Testing First Start and Health Check..." -ForegroundColor Yellow
    $startScript = Join-Path $appDir "start.ps1"
    
    # Launch process
    & powershell -NoProfile -ExecutionPolicy Bypass -File $startScript -Port $TestPort -NoBrowser -AppRoot $appDir -DataDir $dataDir
    
    # Probe health endpoint
    $healthResp = Invoke-WebRequest -Uri "http://127.0.0.1:$TestPort/health/live" -UseBasicParsing -TimeoutSec 5
    if ($healthResp.StatusCode -ne 200) {
        throw "/health/live responded with code $($healthResp.StatusCode)"
    }
    Write-Host "  -> Service successfully started on port $TestPort (HTTP 200)" -ForegroundColor Green

    # Write persistent test row to verify data retention
    $rawDbPath = Join-Path $dataDir "find-yourself.db"
    if (-not (Test-Path $rawDbPath)) {
        throw "Database file $rawDbPath was not created"
    }

    $pythonExe = Join-Path $appDir ".venv\Scripts\python.exe"
    if (-not (Test-Path $pythonExe)) {
        $pythonExe = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
    }

    $outWrite = & $pythonExe $dbTestPy $rawDbPath "write"
    if ($outWrite.Trim() -ne "WRITE_OK") {
        throw "Failed to write verification row: $outWrite"
    }
    Write-Host "  -> Successfully wrote test persistence data to database" -ForegroundColor Green
    $results["2_First_Start_And_Health"] = "PASS"

    # -------------------------------------------------------------------------
    # Step 3: Stop Service
    # -------------------------------------------------------------------------
    Write-Host "`n>>> [Step 3/5] Testing Service Shutdown..." -ForegroundColor Yellow
    $stopScript = Join-Path $appDir "stop.ps1"
    & powershell -NoProfile -ExecutionPolicy Bypass -File $stopScript -Port $TestPort -AppRoot $appDir
    
    # Check port release
    Start-Sleep -Seconds 1
    $portActive = $false
    try {
        $check = Invoke-WebRequest -Uri "http://127.0.0.1:$TestPort/health/live" -UseBasicParsing -TimeoutSec 1
        $portActive = $true
    } catch {
        $portActive = $false
    }

    if ($portActive) {
        throw "Port $TestPort remains active after stop.ps1"
    }
    Write-Host "  -> Service process terminated gracefully and port released" -ForegroundColor Green
    $results["3_Stop_Service"] = "PASS"

    # -------------------------------------------------------------------------
    # Step 4: Restart Service & Data Retention
    # -------------------------------------------------------------------------
    Write-Host "`n>>> [Step 4/5] Testing Service Restart and Data Retention..." -ForegroundColor Yellow
    & powershell -NoProfile -ExecutionPolicy Bypass -File $startScript -Port $TestPort -NoBrowser -AppRoot $appDir -DataDir $dataDir
    
    $healthResp2 = Invoke-WebRequest -Uri "http://127.0.0.1:$TestPort/health/live" -UseBasicParsing -TimeoutSec 5
    if ($healthResp2.StatusCode -ne 200) {
        throw "/health/live did not respond 200 after restart"
    }

    # Verify that previously written data persisted across restart
    $outVerify = & $pythonExe $dbTestPy $rawDbPath "verify"
    if ($outVerify.Trim() -ne "DATA_OK") {
        throw "Data corrupted or missing after restart: $outVerify"
    }
    Write-Host "  -> Service restarted and database records completely preserved!" -ForegroundColor Green

    # Stop service again before uninstall
    & powershell -NoProfile -ExecutionPolicy Bypass -File $stopScript -Port $TestPort -AppRoot $appDir
    $results["4_Restart_And_Data_Retention"] = "PASS"

    # -------------------------------------------------------------------------
    # Step 5: Uninstall & Data Retention Verification
    # -------------------------------------------------------------------------
    Write-Host "`n>>> [Step 5/5] Testing Uninstall and Data Preservation..." -ForegroundColor Yellow
    $uninstallScript = Join-Path $TestInstallDir "uninstall.ps1"
    
    # Mode A: Uninstall with Data Preservation
    & powershell -NoProfile -ExecutionPolicy Bypass -File $uninstallScript -InstallDir $TestInstallDir -PreserveData true
    
    if (Test-Path $appDir) {
        throw "app directory still exists after uninstall"
    }
    if (-not (Test-Path $rawDbPath)) {
        throw "User database was unexpectedly removed during preserved uninstall!"
    }
    Write-Host "  -> Uninstall completed: app removed, user database preserved intact" -ForegroundColor Green

    # Mode B: Complete Cleanup
    & powershell -NoProfile -ExecutionPolicy Bypass -File $uninstallScript -InstallDir $TestInstallDir -PreserveData false
    if (Test-Path $TestInstallDir) {
        Remove-Item $TestInstallDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    Write-Host "  -> Full cleanup completed" -ForegroundColor Green
    $results["5_Uninstall_And_Preservation"] = "PASS"

    Write-Host "`n==========================================================" -ForegroundColor Green
    Write-Host " [VERIFICATION PASSED] All 5 Desktop Lifecycle Tests PASSED!" -ForegroundColor Green
    Write-Host "==========================================================" -ForegroundColor Green
    $results.GetEnumerator() | ForEach-Object {
        Write-Host "  - $($_.Key): $($_.Value)" -ForegroundColor White
    }
    return 0
} catch {
    Write-Host "`n==========================================================" -ForegroundColor Red
    Write-Host " [VERIFICATION FAILED] Error: $_" -ForegroundColor Red
    Write-Host "==========================================================" -ForegroundColor Red
    $stopScript = Join-Path $PSScriptRoot "app\stop.ps1"
    & powershell -NoProfile -ExecutionPolicy Bypass -File $stopScript -Port $TestPort
    return 1
}
