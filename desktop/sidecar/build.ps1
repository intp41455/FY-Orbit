<#
.SYNOPSIS
    把 Find Yourself 后端打成独立 Windows exe（Tauri sidecar）。

.DESCRIPTION
    PyInstaller --onedir，入口 desktop/sidecar/sidecar_main.py。

    选 --onedir 而不是 --onefile：onefile 每次启动都要把整个包解压到临时
    目录，冷启动明显更慢，而且 SQLite / webview profile 之外的资源读取在
    onedir 下路径更稳定（Tauri sidecar 也更容易定位可执行文件）。

    **不内置 Python 解释器，也不内置 Ollama**：发行路径依赖本 exe 自带
    运行时；Ollama 由用户自行安装，设置页只检测连通性。开发路径
    （run_desktop.py）则要求用户已装 Python。两条路径的差异见
    desktop/sidecar/__init__.py 的模块文档。

.PARAMETER ProjectRoot
    项目根目录（默认取本脚本的上两级）。

.PARAMETER Python
    解释器路径（默认用项目 .venv）。

.EXAMPLE
    .\build.ps1
    .\build.ps1 -Clean
#>
[CmdletBinding()]
param(
    [string]$ProjectRoot = "",
    [string]$Python = "",
    [switch]$Clean,
    [switch]$SkipWebBuild
)

$ErrorActionPreference = "Stop"

if (-not $ProjectRoot) {
    $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}
if (-not $Python) {
    $candidate1 = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    $Python = if (Test-Path $candidate1) { $candidate1 } else { "python" }
}

$Entry = Join-Path $ProjectRoot "desktop\sidecar\sidecar_main.py"
$Work = Join-Path $ProjectRoot "desktop\sidecar\build"
$Dist = Join-Path $ProjectRoot "desktop\sidecar\dist"

Write-Host "[sidecar] Project : $ProjectRoot"
Write-Host "[sidecar] Python  : $Python"
Write-Host "[sidecar] Entry   : $Entry"

# 0) 源码来源守卫（历史事故：venv 里的 editable 指向别的目录，PyInstaller 的
#    --collect-all find_yourself 会收走旧副本，发行包静默缺失整块路由模块）。
$resolved = (& $Python -c "import find_yourself, pathlib; print(pathlib.Path(find_yourself.__file__).parent)").Trim()
$expectedPkg = (Join-Path $ProjectRoot "src\find_yourself")
if (-not (Test-Path $expectedPkg)) {
    throw "[sidecar] 找不到 $expectedPkg —— ProjectRoot 传错了吗？"
}
if (-not ($resolved -like "$expectedPkg*")) {
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
    --collect-all "fastapi" `
    --collect-all "anyio" `
    $Entry

if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($LASTEXITCODE)" }

$exe = Join-Path $Dist "find-yourself-backend\find-yourself-backend.exe"
if (-not (Test-Path $exe)) {
    throw "expected sidecar exe not found: $exe"
}

Write-Host ""
Write-Host "[sidecar] OK -> $exe"
Write-Host "[sidecar] Next: desktop\tauri\build.ps1  (needs the Rust toolchain)"
