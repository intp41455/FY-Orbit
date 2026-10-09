<#
.SYNOPSIS
    一键出包：sidecar exe + Tauri NSIS 安装包 -> dist-desktop\FindYourself-Setup.exe

.DESCRIPTION
    分三步，任一步失败立即停止并说明原因（不产出半成品、不谎报成功）：

      1. web 前端构建         npm --prefix web run build
      2. 后端 sidecar 打包    desktop\sidecar\build.ps1（PyInstaller --onedir）
      3. Tauri 壳编译 + NSIS  cargo tauri build

    第 3 步需要 Rust 工具链。若本机没有，本脚本会**明确失败**并给出安装提示，
    不会伪造一个安装包。

.PARAMETER SkipSidecar
    跳过第 2 步（后端 exe 已有且未变化时）。

.PARAMETER SkipWeb
    跳过第 1 步（前端产物已有且未变化时）。

.EXAMPLE
    .\build.ps1
    .\build.ps1 -SkipWeb
#>
[CmdletBinding()]
param(
    [switch]$SkipWeb,
    [switch]$SkipSidecar
)

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$projectRoot = (Resolve-Path (Join-Path $here "..\..")).Path
$srcTauri = Join-Path $here "src-tauri"

Write-Host "=============================================="
Write-Host " Find Yourself · desktop build"
Write-Host " project : $projectRoot"
Write-Host "=============================================="

# --- 0. 前置检查：Rust 工具链 -------------------------------------------------
$hasCargo = $null -ne (Get-Command cargo -ErrorAction SilentlyContinue)
if (-not $hasCargo) {
    Write-Host ""
    Write-Host "[ERROR] 未找到 cargo / rustc，无法编译 Tauri 壳。" -ForegroundColor Red
    Write-Host "       本机交付物 = 完整工程 + 构建脚本；未在本机打包（诚实声明）。"
    Write-Host "       安装 Rust 后重试：https://rustup.rs/"
    Write-Host "         winget install Rustlang.Rustup"
    Write-Host "       或 rustup-init.exe -y"
    Write-Host ""
    Write-Host "       可先单独验证前后两步："
    Write-Host "         powershell -ExecutionPolicy Bypass -File desktop\sidecar\build.ps1"
    exit 3
}

# --- 1. 前端 ------------------------------------------------------------------
if (-not $SkipWeb) {
    Write-Host "[1/3] building web assets ..."
    Push-Location (Join-Path $projectRoot "web")
    try {
        npm run build
        if ($LASTEXITCODE -ne 0) { throw "npm run build failed ($LASTEXITCODE)" }
    } finally { Pop-Location }
} else {
    Write-Host "[1/3] skip web build (-SkipWeb)"
}

# --- 2. sidecar ---------------------------------------------------------------
if (-not $SkipSidecar) {
    Write-Host "[2/3] building python sidecar ..."
    & (Join-Path $projectRoot "desktop\sidecar\build.ps1") -SkipWebBuild
    if ($LASTEXITCODE -ne 0) { throw "sidecar build failed ($LASTEXITCODE)" }
} else {
    Write-Host "[2/3] skip sidecar build (-SkipSidecar)"
}

# --- 3. Tauri 编译 ------------------------------------------------------------
$sidecarExe = Join-Path $projectRoot "desktop\sidecar\dist\find-yourself-backend\find-yourself-backend.exe"
if (-not (Test-Path $sidecarExe)) {
    throw "sidecar exe missing: $sidecarExe (run without -SkipSidecar)"
}

Write-Host "[3/3] cargo tauri build (this takes a while the first time) ..."
# Tauri 需要知道 sidecar exe 的位置：先拷进 resources 目录。
$resDir = Join-Path $srcTauri "sidecar-binaries"
New-Item -ItemType Directory -Path $resDir -Force | Out-Null
Copy-Item $sidecarExe (Join-Path $resDir "find-yourself-backend.exe") -Force

Push-Location $here
try {
    # tauri-cli 优先用 npm 脚本（见 package.json），全局 cargo-tauri 亦可。
    $tauri = Get-Command tauri -ErrorAction SilentlyContinue
    if ($tauri) {
        tauri build
    } else {
        Write-Host "      installing @tauri-apps/cli (npm) ..."
        npm install --no-save --registry=https://registry.npmmirror.com @tauri-apps/cli@latest
        if ($LASTEXITCODE -ne 0) { throw "failed to install @tauri-apps/cli" }
        npx --no-install tauri build
    }
    if ($LASTEXITCODE -ne 0) { throw "cargo tauri build failed ($LASTEXITCODE)" }
} finally { Pop-Location }

# --- 4. 收集产物 --------------------------------------------------------------
$distDesktop = Join-Path $projectRoot "dist-desktop"
New-Item -ItemType Directory -Path $distDesktop -Force | Out-Null

$produced = Get-ChildItem -Path (Join-Path $srcTauri "target\release\bundle") -Recurse -Filter "*.exe" -ErrorAction SilentlyContinue
if (-not $produced) {
    throw "NSIS installer not found under src-tauri\target\release\bundle"
}
$installer = $produced | Where-Object { $_.Name -like "*Setup*" } | Select-Object -First 1
if (-not $installer) { $installer = $produced | Select-Object -First 1 }

$final = Join-Path $distDesktop "FindYourself-Setup.exe"
Copy-Item $installer.FullName $final -Force

Write-Host ""
Write-Host "=============================================="
Write-Host " OK -> $final"
Write-Host " 验收清单见 desktop\tauri\README.md"
Write-Host "=============================================="
