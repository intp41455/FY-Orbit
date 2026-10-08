# 用 Inno Setup 把 .build_stage/FY-Orbit 装成正式 setup.exe
#
# 为什么需要这个脚本而不是直接写 ISCC 命令：
#   * Inno Setup 不在 CI/标准环境里，必须先探测；
#   * setup.exe 文件名要带版本号，而版本号由 git tag / --Version 决定；
#   * 未安装 Inno Setup 时必须如实跳过并返回明确信息，而不是让流水线
#     在「不报错但也没有产物」的灰区里继续。
#
# 前提：先跑 deploy/build_release_package.py --build --keep-stage，
#       确保 .build_stage/FY-Orbit 目录存在。
#
# 用法：
#   powershell -File deploy/installer/build_installer.ps1 [-Version 1.0.0]
[CmdletBinding()]
param(
    [string]$Version = "",
    [switch]$KeepIsoName
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Stage = Join-Path $Root ".build_stage\FY-Orbit"

# 版本号：显式传参 > git tag > 0.0.0-dev
if (-not $Version) {
    $tag = & git -C $Root describe --tags --exact-match HEAD 2>$null
    if ($tag -and $tag -match "^v?(\d+\.\d+\.\d+.*)$") {
        $Version = $Matches[1]
    } else {
        $Version = "0.0.0-dev"
    }
}

# 同步 setup.exe 里的版本号
$IssPath = Join-Path $PSScriptRoot "FY-Orbit.iss"
$content = Get-Content $IssPath -Raw -Encoding UTF8
$content = $content -replace '#define MyAppVersion ".*?"', "#define MyAppVersion `"$Version`""
$tmpIss = Join-Path $env:TEMP "FY-Orbit-$Version.iss"
Set-Content $tmpIss $content -Encoding UTF8

if (-not (Test-Path $Stage)) {
    Write-Host "[installer] 缺少装配目录 $Stage，请先跑 build_release_package.py --build --keep-stage" -ForegroundColor Red
    exit 1
}

$iscc = Get-Command ISCC.exe -ErrorAction SilentlyContinue
if (-not $iscc) {
    $local = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($local) { $iscc = Get-Item $local }
}

if (-not $iscc) {
    Write-Warning "[installer] 未检测到 Inno Setup 6，无法编译 setup.exe。脚本已生成在 $tmpIss。"
    Write-Host "[installer] 绿色 ZIP 可用；要走 ISCC 发行，需先安装 Inno Setup 6。"
    exit 0
}

Write-Host "[installer] ISCC: $($iscc.Source ?? $iscc.FullName)"
Write-Host "[installer] Version: $Version"
& $iscc.Source $tmpIss
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$exe = Join-Path $Root "deploy\installer\output\FY-Orbit-Setup-v$Version.exe"
if (Test-Path $exe) {
    $size = (Get-Item $exe).Length / 1MB
    Write-Host ("[installer] 完成：{0} ({1:N2} MB)" -f $exe, $size) -ForegroundColor Green
} else {
    Write-Host "[installer] ISCC 未产出预期文件：$exe" -ForegroundColor Red
    exit 1
}