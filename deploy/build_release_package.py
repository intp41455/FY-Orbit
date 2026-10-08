#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""装配 FY Orbit · 星轨 Windows 绿色发行包（编译版）。

发行形态（与实际产物一致，不要凭想象改文案）：
  * 后端 = PyInstaller ``--onedir`` 冻结二进制（内置 CPython 运行时与全部依赖），
    用户不需要预装 Python；
  * 前端 = Vite 生产构建产物 ``web/dist``（纯静态），用户不需要 Node.js；
  * 启动器 = ``FY-Orbit.bat`` → ``find-yourself-backend.exe`` → 本地 FastAPI
    → 系统浏览器/Edge App 窗口承载 React 工作台。

也就是说这是一个 **本地 Web 应用打包成的桌面发行物**（Local Web Application
packaged as Desktop Software），不是 Tauri 原生窗口应用。仓库里存在
``desktop/tauri`` 代码，但本脚本产出的发行包与它无关，文案不得混为一谈。

可复现构建
----------
同一 tag 重复构建应得到逐字节一致的 ZIP。为此：

* ZIP 条目按路径排序写入，条目时间戳固定为 ``SOURCE_DATE_EPOCH``（缺省
  ``2026-01-01``），不写入构建机器的当前时间；
* 条目权限位统一规整，不继承工作区里的 ``-rwxr-xr-x``；
* 产物内嵌 ``BUILD-META.txt`` 记录 git commit / tag / 构建脚本版本，
  但**不含构建时间戳**，避免"每次构建都不同"。

用法：
    python deploy/build_release_package.py                 # 用已有构建产物装配
    python deploy/build_release_package.py --build         # 先构建（npm + PyInstaller）再装配
    python deploy/build_release_package.py --out-dir dist  # 产物落到别处

产物：``<out-dir>/FY-Orbit-Windows-<version>.zip`` + 同名 ``.sha256``
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIDECAR = ROOT / "desktop" / "sidecar" / "dist" / "find-yourself-backend"
WEBDIST = ROOT / "web" / "dist"
#: 宣传落地页的**入库权威副本**。仓库根的 ``landing-page-*.html`` 已于 02e1008
#: 删除（临时页 + trycloudflare 死链），线上唯一权威来源是 Cloudflare Pages 目录。
#: 早期版本的本脚本仍指向那个已删文件，导致 ``--build`` 必然 FileNotFoundError。
LANDING = ROOT / "deploy" / "cloudflare" / "dist" / "index.html"
STAGE = ROOT / ".build_stage"
APP = STAGE / "FY-Orbit"

#: ZIP 条目固定时间戳（ZIP 最早支持 1980-01-01）。设为 2026-01-01 是为了
#: 既合法又能和 SOURCE_DATE_EPOCH 语义对齐。
ZIP_EPOCH = (2026, 1, 1, 0, 0, 0)
#: 统一的条目权限：普通文件 0644，目录 0755。
FILE_ATTR = 0o100644 << 16
DIR_ATTR = (0o040755 << 16) | 0x10

VERSION_RE = re.compile(r"^v?(\d+\.\d+\.\d+(?:[-.][0-9A-Za-z.\-]+)?)$")


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), *args],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return out or None


def detect_version(explicit: str | None = None) -> str:
    """确定发行版本号。

    优先级：``--version`` > 当前 commit 上的精确 tag > ``0.0.0-dev``。
    「tag 上构建」是可复现发行链的前提，所以打 tag 后构建必须拿到 tag 号，
    拿不到就落到显眼的 ``0.0.0-dev`` 而不是悄悄沿用上一版号。
    """
    if explicit:
        return explicit.lstrip("v")
    tagged = _git("describe", "--tags", "--exact-match", "HEAD")
    if tagged and VERSION_RE.match(tagged):
        return VERSION_RE.match(tagged).group(1)
    return "0.0.0-dev"


def build_meta(version: str) -> str:
    """产物内嵌的构建溯源信息。刻意不含时间戳——含了就不是可复现构建。"""
    commit = _git("rev-parse", "HEAD") or "unknown"
    short = _git("rev-parse", "--short", "HEAD") or "unknown"
    tag = _git("describe", "--tags", "--exact-match", "HEAD")
    dirty = bool(_git("status", "--porcelain"))
    lines = [
        "FY Orbit · 星轨 — Windows 绿色发行包构建元信息",
        "=" * 60,
        f"version      : {version}",
        f"git commit   : {commit}",
        f"git short    : {short}",
        f"git tag      : {tag or '(none — 非 tag 构建)'}",
        f"git dirty    : {'yes' if dirty else 'no'}",
        f"builder      : deploy/build_release_package.py",
        f"packaging    : PyInstaller --onedir (backend) + Vite production build (web/dist)",
        "runtime need : 无需预装 Python / Node.js（均已内置或已编译为静态文件）",
        "app shape    : 本地 Web 应用打包为桌面发行物（浏览器 / Edge App 窗口承载 UI）",
        "",
        "复现方式：",
        f"  git checkout {tag or '<commit>'}",
        "  python deploy/build_release_package.py --build",
    ]
    if dirty:
        lines += [
            "",
            "警告：构建时工作区存在未提交改动，产物无法由该 commit 复现。",
        ]
    return "\n".join(lines) + "\n"


LAUNCHER_BAT = """@echo off
chcp 65001 >nul
title FY Orbit · 星轨 (Windows 绿色独立版)
echo ============================================================
echo       FY Orbit · 星轨 v{version} (Windows 绿色免安装版)
echo       本地优先的多智能体编排与统一调度平台
echo ============================================================
echo.
echo [1/4] 正在准备本地运行环境...
set ROOT=%~dp0
cd /d "%ROOT%"

set BACKEND_EXE="%ROOT%find-yourself-backend\\find-yourself-backend.exe"
if not exist %BACKEND_EXE% (
    echo [错误] 未找到核心程序 find-yourself-backend.exe！
    echo 请确认已完整解压压缩包中的所有文件与子目录。
    pause
    exit /b 1
)

echo [2/4] 配置静态资源与工作目录...
if exist "%ROOT%web\\dist" set FY_STATIC_DIR=%ROOT%web\\dist

set FY_ENVIRONMENT=local
set FY_OFFLINE_MODE=1
set FY_LOCAL_ONLY=1

rem --- 完整性自检（可复现构建链的一部分：SHA256SUMS.txt 与本包一同分发）---
if exist "%ROOT%verify.bat" (
    echo [3/4] 校验发行包完整性...
    call "%ROOT%verify.bat"
    if errorlevel 1 (
        echo.
        echo [错误] 完整性校验未通过，已中止启动。
        pause
        exit /b 1
    )
) else (
    echo [3/4] 未找到 verify.bat，跳过完整性校验。
)

echo [4/4] 正在启动应用引擎与核心服务 (默认端口 8000)...
echo.
echo ************************************************************
echo  服务启动后，系统将自动打开工作台窗口。
echo  工作台地址: http://127.0.0.1:8000
echo.
echo  如需退出应用，请直接按 Ctrl+C 或关闭本控制台窗口。
echo ************************************************************
echo.

%BACKEND_EXE% --host 127.0.0.1 --port 8000 %*

if %ERRORLEVEL% neq 0 (
    echo.
    echo [提示] 应用程序已退出 (Exit code: %ERRORLEVEL%)。
    pause
)
"""

README_TXT = """================================================================================
  FY Orbit · 星轨 v{version} Windows 绿色免安装版
  本地优先的多智能体编排与统一调度平台
================================================================================

【开箱即用说明】
本软件包已内置独立编译的完整二进制运行时与前端全套静态应用，解压即用。
无需预装 Python、Node.js 或任何开发工具链。

【这是什么形态的软件（如实说明）】
本发行包是「本地 Web 应用打包成的桌面发行物」：
  后端 find-yourself-backend.exe（内置 CPython 运行时）
      ↓ 监听 127.0.0.1:8000
  FastAPI 同时伺服 React 静态工作台
      ↓
  系统默认浏览器 / Edge App 独立窗口承载 UI
因此它不是 Tauri 原生窗口应用；界面由浏览器引擎渲染，这是刻意的架构取舍
（与 VS Code / Jupyter 等工具的 Local Server + Web UI 路线一致）。

【系统要求】
  操作系统：Windows 10 22H2 / Windows 11（x64）
  内存    ：4 GB 及以上
  磁盘    ：约 250 MB（含运行时），数据目录另计
不承诺 Windows 7/8/Server 或 32 位系统兼容。

【安全提示：SmartScreen 提示】
本发行包目前**未做 Authenticode 代码签名**（无付费代码签名证书）。
Windows Defender SmartScreen 可能对首次运行的未知发布者弹出
"Windows 已保护你的电脑"。校验办法见下方【校验发行包完整性】——
请以 SHA-256 比对结果为准，不要直接关闭防护。

【启动方式】
1. 双击运行当前目录下的「FY-Orbit.bat」；
2. 启动器会先做完整性自检（若包内有 verify.bat），通过后拉起核心引擎，
   并自动打开工作台窗口：
   工作台地址: http://127.0.0.1:8000
3. 产品介绍页：http://127.0.0.1:8000/landing.html

【目录结构说明】
  FY-Orbit.bat                  # 一键启动脚本（推荐双击启动）
  verify.bat                    # 发行包完整性自检（SHA-256）
  SHA256SUMS.txt                # 校验和清单（与 GitHub Release 中的一致）
  BUILD-META.txt                # 构建溯源（版本 / commit / tag）
  landing.html                  # 官方全景介绍与特性展示页
  使用说明.txt                  # 本说明文档
  find-yourself-backend/        # 独立编译的完整二进制核心引擎及运行时动态库
  web/dist/                     # 完整前端静态编译产物
  data/                         # 本地数据库与持久化数据目录（首次启动自动创建）

【校验发行包完整性】
在 PowerShell 中执行：
    Get-FileHash .\\FY-Orbit-Windows-v{version}.zip -Algorithm SHA256
与 GitHub Release 页面公布的 SHA-256 比对一致即说明文件未被损坏或替换。
（本包解出后的 verify.bat 校验的是包内文件与 SHA256SUMS.txt 的一致性。）

【进阶与参数说明】
- 默认端口：8000；若被占用可通过命令行指定：FY-Orbit.bat --port 8080
- 不需要自动打开窗口：FY-Orbit.bat --no-browser

【如何退出】
在命令行窗口按 Ctrl+C，或直接关闭该窗口即可退出全部后台服务。

【如何卸载】
删除安装目录即可。数据目录位于程序目录下的 data\\（或 %LOCALAPPDATA%\\FYOrbit\\data），
如需彻底清除请一并删除。桌面/开始菜单快捷方式由 Install.bat 创建，
可用其生成的 uninstall.bat 清除。
"""


INSTALL_BAT = """@echo off
chcp 65001 >nul
title FY Orbit · 星轨 安装
setlocal
set SRC=%~dp0
set DEST=%LOCALAPPDATA%\\FYOrbit

echo 正在安装到 %DEST% ...
if not exist "%DEST%" mkdir "%DEST%"
robocopy "%SRC%." "%DEST%" /E /NFL /NDL /NJH /NJS /NC /NS >nul

echo 正在创建快捷方式 ...
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell); $d=[Environment]::GetFolderPath('Desktop'); $p=Join-Path $d 'FY Orbit 星轨.lnk'; $l=$s.CreateShortcut($p); $l.TargetPath='%DEST%\\FY-Orbit.bat'; $l.WorkingDirectory='%DEST%'; $l.IconLocation='%DEST%\\web\\dist\\favicon.ico'; $l.Save(); $m=Join-Path ([Environment]::GetFolderPath('Programs')) 'FY Orbit 星轨.lnk'; $l2=$s.CreateShortcut($m); $l2.TargetPath='%DEST%\\FY-Orbit.bat'; $l2.WorkingDirectory='%DEST%'; $l2.IconLocation='%DEST%\\web\\dist\\favicon.ico'; $l2.Save()"

>"%DEST%\\uninstall.bat" echo @echo off
>>"%DEST%\\uninstall.bat" echo taskkill /f /im find-yourself-backend.exe ^>nul 2^>nul
>>"%DEST%\\uninstall.bat" echo del /q "%%USERPROFILE%%\\Desktop\\FY Orbit 星轨.lnk" ^>nul 2^>nul
>>"%DEST%\\uninstall.bat" echo del /q "%%APPDATA%%\\Microsoft\\Windows\\Start Menu\\Programs\\FY Orbit 星轨.lnk" ^>nul 2^>nul
>>"%DEST%\\uninstall.bat" echo echo 已卸载（用户数据保留在 %DEST%\\data）
>>"%DEST%\\uninstall.bat" echo pause

echo.
echo 安装完成：桌面与开始菜单已创建「FY Orbit 星轨」快捷方式。
echo 卸载：运行 %DEST%\\uninstall.bat
echo.
echo 说明：本绿色包无需安装即可运行，Install.bat 只是为需要固定安装目录与
echo 快捷方式的场景准备的轻量安装器，不是 MSI/MSIX 正式安装包。
echo.
pause
"""


VERIFY_BAT = """@echo off
rem 发行包完整性自检：逐个文件比对 SHA256SUMS.txt
chcp 65001 >nul
setlocal enabledelayedexpansion
set ROOT=%~dp0
cd /d "%ROOT%"

if not exist "%ROOT%SHA256SUMS.txt" (
    echo [verify] 未找到 SHA256SUMS.txt，无法校验。
    exit /b 0
)

set FAILED=0
set COUNT=0
for /f "usebackq tokens=1,2" %%A in ("%ROOT%SHA256SUMS.txt") do (
    if not "%%A"=="#" (
        set /a COUNT+=1
        set EXPECTED=%%A
        set TARGET=%%B
        if not exist "%ROOT%!TARGET!" (
            echo [verify] 缺失文件: !TARGET!
            set /a FAILED+=1
        ) else (
            for /f "usebackq delims=" %%H in (`powershell -NoProfile -Command "(Get-FileHash -LiteralPath '%ROOT%!TARGET!' -Algorithm SHA256).Hash.ToLower()"`) do set ACTUAL=%%H
            if not "!ACTUAL!"=="!EXPECTED!" (
                echo [verify] 校验不通过: !TARGET!
                echo           期望 !EXPECTED!
                echo           实际 !ACTUAL!
                set /a FAILED+=1
            )
        )
    )
)

if %FAILED% gtr 0 (
    echo.
    echo [verify] 完整性校验失败：%FAILED% / %COUNT% 个文件不匹配。
    exit /b 1
)
echo [verify] 完整性校验通过：%COUNT% 个文件与 SHA256SUMS.txt 一致。
exit /b 0
"""


def run(cmd, cwd=None) -> None:
    print("  $", " ".join(str(c) for c in cmd))
    subprocess.run([str(c) for c in cmd], cwd=cwd, check=True)


def build() -> None:
    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    print("[build] 前端生产构建 ...")
    run([npm, "run", "build"], cwd=ROOT / "web")
    print("[build] 后端二进制（PyInstaller onedir）...")
    run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", ROOT / "desktop" / "sidecar" / "build.ps1",
         "-ProjectRoot", ROOT, "-SkipWebBuild"])


def write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding=encoding, newline="\r\n" if path.suffix == ".bat" else None)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _zip_write(zf: zipfile.ZipFile, arcname: str, src: Path) -> None:
    """按固定时间戳与固定权限写入，保证同输入 → 同字节。"""
    info = zipfile.ZipInfo(arcname, date_time=ZIP_EPOCH)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = FILE_ATTR
    info.create_system = 0  # 固定为 FAT/MS-DOS，避免跨平台 create_system 差异
    zf.writestr(info, src.read_bytes())


def _package_filesums(app_dir: Path) -> str:
    """对包内关键文件求 SHA-256，写成 verify.bat 可读的清单。"""
    targets = ["FY-Orbit.bat", "Install.bat", "使用说明.txt", "BUILD-META.txt", "landing.html"]
    lines = ["# FY Orbit · 星轨 — 发行包内文件校验和（SHA-256）",
             "# 由 deploy/build_release_package.py 在打包时生成，可与 GitHub Release 的",
             "# 顶层 ZIP 校验和相互印证。"]
    for rel in targets:
        p = app_dir / rel
        if p.is_file():
            lines.append(f"{sha256_file(p)}  {rel}")
    return "\n".join(lines) + "\n"


def assemble(version: str, out_dir: Path, *, keep_stage: bool = False) -> tuple[Path, str]:
    if not (SIDECAR / "find-yourself-backend.exe").is_file():
        raise SystemExit("缺少编译产物 desktop/sidecar/dist/find-yourself-backend/，"
                         "先跑 desktop/sidecar/build.ps1 或加 --build")
    if not (WEBDIST / "index.html").is_file():
        raise SystemExit("缺少前端产物 web/dist/，先跑 npm --prefix web run build 或加 --build")
    if not LANDING.is_file():
        raise SystemExit(f"缺少落地页权威副本 {LANDING}（仓库根的临时落地页已于 02e1008 删除）")

    if STAGE.exists():
        shutil.rmtree(STAGE)
    APP.mkdir(parents=True, exist_ok=True)

    print("[1/5] 后端编译产物 ...")
    shutil.copytree(SIDECAR, APP / "find-yourself-backend",
                    ignore=shutil.ignore_patterns("*.pyc", "__pycache__"))

    print("[2/5] 前端静态产物 ...")
    shutil.copytree(WEBDIST, APP / "web" / "dist", ignore=shutil.ignore_patterns("*.map"))

    print("[3/5] 落地页 ...")
    landing = LANDING.read_text(encoding="utf-8")
    (APP / "landing.html").write_text(landing, encoding="utf-8")
    (APP / "web" / "dist" / "landing.html").write_text(landing, encoding="utf-8")

    print("[4/5] 说明、启动器与完整性校验 ...")
    shutil.copy2(ROOT / "README.md", APP / "README.md")
    if (ROOT / ".env.example").is_file():
        shutil.copy2(ROOT / ".env.example", APP / ".env.example")
    write_text(APP / "FY-Orbit.bat", LAUNCHER_BAT.format(version=version), encoding="gbk")
    write_text(APP / "使用说明.txt", README_TXT.format(version=version), encoding="utf-8")
    write_text(APP / "Install.bat", INSTALL_BAT, encoding="gbk")
    write_text(APP / "verify.bat", VERIFY_BAT, encoding="gbk")
    write_text(APP / "BUILD-META.txt", build_meta(version), encoding="utf-8")
    write_text(APP / "SHA256SUMS.txt", _package_filesums(APP), encoding="utf-8")

    print("[5/5] 打包（确定性 ZIP：条目排序 + 固定时间戳）...")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_zip = out_dir / f"FY-Orbit-Windows-v{version}.zip"
    if out_zip.exists():
        out_zip.unlink()
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for p in sorted(APP.rglob("*")):
            rel = str(p.relative_to(STAGE)).replace(os.sep, "/")
            if p.is_dir():
                info = zipfile.ZipInfo(rel + "/", date_time=ZIP_EPOCH)
                info.external_attr = DIR_ATTR
                info.create_system = 0
                zf.writestr(info, b"")
            elif p.is_file():
                _zip_write(zf, rel, p)
    digest = sha256_file(out_zip)
    write_text(out_dir / f"FY-Orbit-Windows-v{version}.zip.sha256",
               f"{digest}  FY-Orbit-Windows-v{version}.zip\n", encoding="utf-8")
    if not keep_stage:
        shutil.rmtree(STAGE)
    else:
        print(f"[keep-stage] 已保留装配目录供 Inno Setup 复用：{STAGE}")
    print(f"完成：{out_zip} ({out_zip.stat().st_size / 1024 / 1024:.2f} MB)")
    print(f"SHA-256: {digest}")
    return out_zip, digest


def main() -> int:
    ap = argparse.ArgumentParser(description="装配 Windows 绿色发行包")
    ap.add_argument("--build", action="store_true", help="先构建前端与后端二进制再装配")
    ap.add_argument("--version", default=None, help="发行版本号（默认取当前 HEAD 上的 tag）")
    ap.add_argument("--out-dir", default=None, help="产物输出目录（默认仓库根）")
    ap.add_argument("--keep-stage", action="store_true",
                    help="保留装配目录 .build_stage/（供 Inno Setup 脚本 ISCC 直接消费）")
    args = ap.parse_args()
    if args.build:
        build()
    version = detect_version(args.version)
    if version == "0.0.0-dev":
        print("[warn] 当前 HEAD 上没有版本 tag，产物版本号退化为 0.0.0-dev；"
              "正式发行请先打 tag（可复现发行链要求产物由 tag 唯一确定）。")
    out_dir = Path(args.out_dir).resolve() if args.out_dir else ROOT
    assemble(version, out_dir, keep_stage=args.keep_stage)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())