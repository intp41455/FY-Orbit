import os
import shutil
import zipfile
from pathlib import Path

ROOT = Path(r"c:\Users\intpj\Documents\Codex\2026-09-29\agent\outputs\fy-finish")
OUT_ZIP = ROOT / "FY-Orbit-Windows-v1.0.0.zip"
STAGE_DIR = ROOT / ".build_stage"

if STAGE_DIR.exists():
    shutil.rmtree(STAGE_DIR)
STAGE_DIR.mkdir(parents=True, exist_ok=True)

APP_DIR = STAGE_DIR / "FY-Orbit"
APP_DIR.mkdir(parents=True, exist_ok=True)

print("==> 1. Copying backend src...")
shutil.copytree(ROOT / "src", APP_DIR / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyd"))

print("==> 2. Copying frontend web dist...")
shutil.copytree(ROOT / "web" / "dist", APP_DIR / "web", ignore=shutil.ignore_patterns("*.map"))

print("==> 3. Copying landing page and docs...")
shutil.copy2(ROOT / "landing-page-2026-10-06.html", APP_DIR / "landing.html")
shutil.copy2(ROOT / "README.md", APP_DIR / "README.md")
if (ROOT / "LICENSE").exists():
    shutil.copy2(ROOT / "LICENSE", APP_DIR / "LICENSE")

print("==> 4. Creating launcher scripts...")
launcher_bat = """@echo off
title FY Orbit · 星轨
chcp 65001 >nul
cd /d "%~dp0"
echo ==============================================================================
echo   FY Orbit · 星轨 —— 企业级智能体调度中枢与自适应工作流工坊
echo   100%% 本地优先 · 零公网依赖 · 开箱即用
echo ==============================================================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [提示] 启动异常，按任意键退出...
    pause >nul
)
"""
(APP_DIR / "FY-Orbit.bat").write_text(launcher_bat, encoding="gbk")
(APP_DIR / "start.bat").write_text(launcher_bat, encoding="gbk")

start_ps1 = """# FY Orbit Desktop Launcher (start.ps1)
[CmdletBinding()]
param (
    [int]$Port = 8088,
    [switch]$NoBrowser,
    [string]$AppRoot = ""
)

$ErrorActionPreference = "Stop"
if (-not $AppRoot) { $AppRoot = $PSScriptRoot }

$DataDir = "$env:LOCALAPPDATA\\FYOrbit\\data"
$RunDir = Join-Path $AppRoot "run"
if (-not (Test-Path $RunDir)) { New-Item -ItemType Directory -Path $RunDir -Force | Out-Null }
if (-not (Test-Path $DataDir)) { New-Item -ItemType Directory -Path $DataDir -Force | Out-Null }

$PidFile = Join-Path $RunDir "fyorbit.pid"
if (Test-Path $PidFile) {
    $existingPid = Get-Content $PidFile -ErrorAction SilentlyContinue
    if ($existingPid -and (Get-Process -Id $existingPid -ErrorAction SilentlyContinue)) {
        Write-Host "[FY Orbit] 服务已在后台运行 (PID: $existingPid, 端口: $Port)" -ForegroundColor Yellow
        if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$Port" }
        exit 0
    } else {
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    }
}

$PythonCmd = $null
$possiblePythons = @(
    (Join-Path $AppRoot "runtime\\python.exe"),
    (Join-Path $AppRoot ".venv\\Scripts\\python.exe"),
    (Join-Path $AppRoot "..\\.venv\\Scripts\\python.exe"),
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
    Write-Error "[FY Orbit 错误] 未检测到 Python 运行时。请确保系统已安装 Python 3.11+ 或将其置于 runtime 目录。"
    exit 1
}

$srcDir = Join-Path $AppRoot "src"
$env:PYTHONPATH = "$srcDir;$env:PYTHONPATH"

$DbPath = Join-Path $DataDir "find-yourself.db"
$StaticDir = Join-Path $AppRoot "web"

$env:FY_ENVIRONMENT = "local"
$env:FY_SESSION_SECRET = "desktop-session-secret-local-32chars-min-key!"
$env:FY_LOCAL_TOKEN = "desktop-token-secret"
$env:FY_DATABASE_URL = "sqlite:///$($DbPath -replace '\\\\', '/')"
$env:FY_PUBLIC_URL = "http://127.0.0.1:$Port"
$env:FY_STATIC_DIR = (Resolve-Path $StaticDir).Path

$LogFile = Join-Path $RunDir "app.log"
$ErrLogFile = Join-Path $RunDir "app.err.log"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " [FY Orbit · 星轨] 正在拉起核心服务..." -ForegroundColor Cyan
Write-Host " 端口: $Port | 数据存储: $DbPath" -ForegroundColor Gray
Write-Host "==========================================================" -ForegroundColor Cyan

$proc = Start-Process -FilePath $PythonCmd `
    -ArgumentList "-m uvicorn find_yourself.api.app:create_app --factory --host 127.0.0.1 --port $Port --log-level info" `
    -WorkingDirectory $AppRoot `
    -NoNewWindow `
    -PassThru `
    -RedirectStandardOutput $LogFile `
    -RedirectStandardError $ErrLogFile

$proc.Id | Out-File $PidFile -Encoding ascii

$healthUrl = "http://127.0.0.1:$Port/health/live"
$timeoutSec = 20
$deadline = (Get-Date).AddSeconds($timeoutSec)
$isHealthy = $false

while ((Get-Date) -lt $deadline) {
    if ($proc.HasExited) {
        Write-Error "[FY Orbit 错误] 进程异常退出。请查看日志: $LogFile"
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
    Write-Error "[FY Orbit 错误] 启动超时: $healthUrl 未在 ${timeoutSec}s 内就绪。"
    exit 1
}

Write-Host " [FY Orbit · 星轨] 核心服务就绪! (PID: $($proc.Id))" -ForegroundColor Green
Write-Host " 访问地址: http://127.0.0.1:$Port" -ForegroundColor Green

if (-not $NoBrowser) {
    $edgeExe = $null
    $edgeCandidates = @(
        "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
        "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
        "$env:LOCALAPPDATA\\Microsoft\\Edge\\Application\\msedge.exe"
    )
    foreach ($cand in $edgeCandidates) {
        if (Test-Path $cand) {
            $edgeExe = $cand
            break
        }
    }

    if ($edgeExe) {
        Write-Host " [FY Orbit] 启动独立原生工作台窗口 (Edge App 模式)..." -ForegroundColor Cyan
        $webviewDir = Join-Path $RunDir "webview-profile"
        Start-Process -FilePath $edgeExe `
            -ArgumentList "--app=http://127.0.0.1:$Port", "--window-size=1440,900", "--user-data-dir=`"$webviewDir`"", "--no-first-run", "--no-default-browser-check"
    } else {
        Start-Process "http://127.0.0.1:$Port"
    }
}
"""
(APP_DIR / "start.ps1").write_text(start_ps1, encoding="utf-8")

stop_ps1 = """# FY Orbit Stop Script (stop.ps1)
$RunDir = Join-Path $PSScriptRoot "run"
$PidFile = Join-Path $RunDir "fyorbit.pid"

if (Test-Path $PidFile) {
    $existingPid = Get-Content $PidFile -ErrorAction SilentlyContinue
    if ($existingPid) {
        Stop-Process -Id $existingPid -Force -ErrorAction SilentlyContinue
        Write-Host "[FY Orbit] 服务已终止 (PID: $existingPid)" -ForegroundColor Yellow
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
} else {
    Write-Host "[FY Orbit] 未发现正在运行的服务 PID 文件。" -ForegroundColor Gray
}
"""
(APP_DIR / "stop.ps1").write_text(stop_ps1, encoding="utf-8")

config_env = """# FY Orbit 本地桌面配置 (config.env)
FY_ENVIRONMENT=local
FY_SESSION_SECRET=desktop-session-secret-local-32chars-min-key!
FY_LOCAL_TOKEN=desktop-token-secret
FY_OFFLINE_MODE=1
"""
(APP_DIR / "config.env").write_text(config_env, encoding="utf-8")

print(f"==> 5. Creating ZIP package: {OUT_ZIP}...")
with zipfile.ZipFile(OUT_ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
    for root, dirs, files in os.walk(STAGE_DIR):
        for file in files:
            full_path = Path(root) / file
            rel_path = full_path.relative_to(STAGE_DIR)
            zf.write(full_path, str(rel_path))

print(f"==> Package created successfully! Size: {OUT_ZIP.stat().st_size / (1024 * 1024):.2f} MB")
shutil.rmtree(STAGE_DIR)
