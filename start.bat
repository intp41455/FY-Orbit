@echo off
chcp 65001 >nul
title FY Orbit · 星轨 Launcher
echo ===================================================
echo     FY Orbit · 星轨 - 本地一体化应用启动器
echo ===================================================
echo.

set ROOT=%~dp0
cd /d "%ROOT%"

set PYTHON_CMD=
if exist "%ROOT%\.venv\Scripts\python.exe" (
    set PYTHON_CMD="%ROOT%\.venv\Scripts\python.exe"
) else if exist "C:\Users\intpj\.workbuddy\binaries\python\envs\fy-p10\Scripts\python.exe" (
    set PYTHON_CMD="C:\Users\intpj\.workbuddy\binaries\python\envs\fy-p10\Scripts\python.exe"
) else (
    where python >nul 2>nul
    if %ERRORLEVEL% equ 0 (
        set PYTHON_CMD=python
    )
)

if "%PYTHON_CMD%"=="" (
    echo [ERROR] 未找到 Python 环境！请确保安装了 Python 3.11+ 或创建了 .venv 虚拟环境。
    pause
    exit /b 1
)

echo [INFO] 使用 Python: %PYTHON_CMD%
echo [INFO] 正在启动应用与服务...
%PYTHON_CMD% "%ROOT%\run.py" %*

pause
