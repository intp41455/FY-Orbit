#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""装配 FY Orbit · 星轨 Windows 绿色发行包（编译版）。

与实际发行形态一致：
  * 后端 = PyInstaller 编译产物（内置运行时，用户免装 Python / Node）
  * 前端 = Vite 生产构建产物（web/dist）

用法：
    python deploy/build_release_package.py            # 用已有构建产物装配
    python deploy/build_release_package.py --build    # 先构建（npm build + PyInstaller）再装配

产物：<repo>/FY-Orbit-Windows-v1.0.0.zip（内含顶层 FY-Orbit/）
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIDECAR = ROOT / "desktop" / "sidecar" / "dist" / "find-yourself-backend"
WEBDIST = ROOT / "web" / "dist"
LANDING = ROOT / "landing-page-2026-10-06.html"
STAGE = ROOT / ".build_stage"
APP = STAGE / "FY-Orbit"
OUT_ZIP = ROOT / "FY-Orbit-Windows-v1.0.0.zip"

LAUNCHER_BAT = """@echo off
chcp 65001 >nul
title FY Orbit · 星轨 (Windows 绿色独立版)
echo ============================================================
echo       FY Orbit · 星轨 v1.0.0 (Windows 绿色免安装版)
echo       本地优先的多智能体编排与统一调度平台
echo ============================================================
echo.
echo [1/3] 正在准备本地运行环境...
set ROOT=%~dp0
cd /d "%ROOT%"

set BACKEND_EXE="%ROOT%find-yourself-backend\\find-yourself-backend.exe"
if not exist %BACKEND_EXE% (
    echo [错误] 未找到核心程序 find-yourself-backend.exe！
    echo 请确认已完整解压压缩包中的所有文件与子目录。
    pause
    exit /b 1
)

echo [2/3] 配置静态资源与工作目录...
if exist "%ROOT%web\\dist" set FY_STATIC_DIR=%ROOT%web\\dist

set FY_ENVIRONMENT=local
set FY_OFFLINE_MODE=1
set FY_LOCAL_ONLY=1

echo [3/3] 正在启动应用引擎与核心服务 (默认端口 8000)...
echo.
echo ************************************************************
echo  服务启动后，系统将自动在默认浏览器中打开工作台。
echo  工作台地址: http://127.0.0.1:8000
echo  产品展示页: http://127.0.0.1:8000/landing.html
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
  FY Orbit · 星轨 v1.0.0 Windows 绿色免安装版
  本地优先的多智能体编排与统一调度平台
================================================================================

【开箱即用说明】
本软件包已内置独立编译的完整二进制运行时与前端全套静态应用，解压即用。
在任何 64 位 Windows 系统上运行无需预装 Python、Node.js 或任何开发工具链。

【启动方式】
1. 双击运行当前目录下的「FY-Orbit.bat」（或「start.bat」）；
2. 启动后终端将自动拉起核心引擎，并自动用系统默认浏览器打开工作台：
   控制台地址: http://127.0.0.1:8000
3. 若需查阅官方介绍与特性展示，可双击打开当前目录下的「landing.html」，或在服务启动后访问：
   产品展示页: http://127.0.0.1:8000/landing.html

【目录结构说明】
  FY-Orbit.bat                  # 一键启动脚本（推荐双击启动）
  start.bat                     # 快捷启动入口
  landing.html                  # 官方全景介绍与特性展示页
  使用说明.txt                  # 本说明文档
  find-yourself-backend/        # 独立编译的完整二进制核心引擎及运行时动态库
  web/dist/                     # 完整前端静态编译产物
  data/                         # 本地数据库与持久化数据目录（首次启动自动创建）

【进阶与参数说明】
- 默认端口：8000；若被占用可通过命令行指定：FY-Orbit.bat --port 8080
- 不需要自动弹出浏览器：FY-Orbit.bat --no-browser

【如何退出】
在命令行窗口按 Ctrl+C，或直接关闭该窗口即可退出全部后台服务。
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


def assemble() -> None:
    if not (SIDECAR / "find-yourself-backend.exe").is_file():
        raise SystemExit("缺少编译产物 desktop/sidecar/dist/find-yourself-backend/，"
                         "先跑 desktop/sidecar/build.ps1 或加 --build")
    if not (WEBDIST / "index.html").is_file():
        raise SystemExit("缺少前端产物 web/dist/，先跑 npm --prefix web run build 或加 --build")

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

    print("[4/5] 说明与启动器 ...")
    shutil.copy2(ROOT / "README.md", APP / "README.md")
    if (ROOT / ".env.example").is_file():
        shutil.copy2(ROOT / ".env.example", APP / ".env.example")
    write_text(APP / "FY-Orbit.bat", LAUNCHER_BAT, encoding="gbk")
    write_text(APP / "使用说明.txt", README_TXT, encoding="utf-8")

    print("[5/5] 打包 ...")
    with zipfile.ZipFile(OUT_ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for p in sorted(STAGE.rglob("*")):
            if p.is_file():
                zf.write(p, str(p.relative_to(STAGE)))
    shutil.rmtree(STAGE)
    print("完成：%s (%.2f MB)" % (OUT_ZIP, OUT_ZIP.stat().st_size / 1024 / 1024))


def main() -> int:
    ap = argparse.ArgumentParser(description="装配 Windows 绿色发行包")
    ap.add_argument("--build", action="store_true", help="先构建前端与后端二进制再装配")
    args = ap.parse_args()
    if args.build:
        build()
    assemble()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())