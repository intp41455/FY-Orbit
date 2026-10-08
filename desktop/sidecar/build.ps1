<#====================================================================
  find-yourself sidecar 构建脚本
  用法：.\build.ps1 [-Clean] [-SkipWebBuild] [-ProjectRoot <dir>]
====================================================================#>
[CmdletBinding()]
param(
    [switch] $Clean,
    [switch] $SkipWebBuild,
    [string] $ProjectRoot = (Get-Location).Path
)

$ErrorActionPreference = "Stop"

$Python = Join-Path $env:LOCALAPPDATA "uv" "python" "cpython-3.13-windows-x86_64-none" "python.exe"
if (-not (Test-Path $Python)) {
    $Python = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $Python) {
        throw "[sidecar] 找不到 python。请确保已 `uv python install 3.13` 或把 python 加入 PATH。"
    }
}

$Entry = Join-Path $ProjectRoot "desktop\sidecar\sidecar_main.py"
$Work  = Join-Path $ProjectRoot "desktop\sidecar\build"
$Dist  = Join-Path $ProjectRoot "desktop\sidecar\dist"

Write-Host "[sidecar] Project : $ProjectRoot"
Write-Host "[sidecar] Python  : $Python"
Write-Host "[sidecar] Entry   : $Entry"

# 0) 源码来源守卫（历史事故：venv 里的 editable 指向别的目录，PyInstaller 的
#    --collect-all find_yourself 会收走旧副本，发行包静默缺失整块路由模块）。
$resolved = (& $Python -c "import find_yourself, pathlib; print(pathlib.Path(find_yourself.__file__).parent)").Trim()
$expectedPkg = Join-Path $ProjectRoot "src\find_yourself"
if (-not (Test-Path $expectedPkg)) {
    throw "[sidecar] 找不到 $expectedPkg —— ProjectRoot 传错了吗？"
}
if (-not ($resolved -like "'$expectedPkg*'")) {
    throw "[sidecar] find_yourself 解析到 $resolved，不在本仓库 src（$expectedPkg）。发行包会装错源码，请先修正 venv 的 editable 安装。"
}
Write-Host "[sidecar] source  : $resolved  (OK)"

if ($Clean -and (Test-Path $Work)) {
    Write-Host "[sidecar] cleaning $Work"
    Remove-Item $Work -Recurse -Force
}

# 1) 前端静态产物：后端要能把它作为静态目录伺服给 Tauri 窗口。
if (-not $SkipWebBuild) {
    $webDir = Join-Path $ProjectRoot "web"
    if (Test-Path (Join-Path $webDir "package.json")) {
        Write-Host "[sidecar] building web assets (npm run build) ..."
        Push-Location $webDir
        try {
            npm run build
            if ($LASTEXITCODE -ne 0) { throw "npm run build failed ($LASTEXITCODE)" }
        } finally { Pop-Location }
    } else {
        Write-Warning "[sidecar] web/package.json not found; sidecar will run API-only."
    }
}

# 2) 打包。
Write-Host "[sidecar] running PyInstaller --onedir ..."
& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --name "find-yourself-backend" `
    --distpath $Dist `
    --workpath $Work `
    --specpath $Work `
    --paths (Join-Path $ProjectRoot "src") `
    --hidden-import "uvicorn" `
    --hidden-import "uvicorn.logging" `
    --hidden-import "uvicorn.loops" `
    --hidden-import "uvicorn.protocols" `
    --hidden-import "uvicorn.protocols.http" `
    --hidden-import "uvicorn.protocols.http.auto" `
    --hidden-import "uvicorn.protocols.http.h11_impl" `
    --hidden-import "uvicorn.protocols.websockets" `
    --hidden-import "uvicorn.protocols.websockets.auto" `
    --hidden-import "uvicorn.protocols.websockets.wsproto_impl" `
    --hidden-import "uvicorn.lifespan" `
    --hidden-import "uvicorn.lifespan.on" `
    --hidden-import "uvicorn.lifespan.off" `
    --hidden-import "sqlalchemy.dialects.sqlite" `
    --hidden-import "mss" `
    --hidden-import "pyautogui" `
    --hidden-import "pygetwindow" `
    --hidden-import "pyscreeze" `
    --hidden-import "pyrect" `
    --hidden-import "pyperclip" `
    --hidden-import "pymsgbox" `
    --hidden-import "pytweening" `
    --hidden-import "mouseinfo" `
    --hidden-import "PIL" `
    --hidden-import "PIL.Image" `
    --hidden-import "yaml" `
    --hidden-import "dotenv" `
    --collect-all "find_yourself" `
    --collect-all "uvicorn" `
    --collect-all "starlette" `
    $Entry

$Exe = Join-Path $Dist "find-yourself-backend" "find-yourself-backend.exe"
if (-not (Test-Path $Exe)) {
    throw "[sidecar] 未生成 exe：$Exe"
}
Write-Host "[sidecar] done -> $Exe"

