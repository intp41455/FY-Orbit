@echo off
chcp 65001 >nul
title FY Orbit · 星轨 Launcher
set ROOT=%~dp0
cd /d "%ROOT%"

if exist "%ROOT%FY-Orbit.bat" (
    call "%ROOT%FY-Orbit.bat" %*
    exit /b %ERRORLEVEL%
)

if exist "%ROOT%find-yourself-backend\find-yourself-backend.exe" (
    set FY_STATIC_DIR=%ROOT%web\dist
    "%ROOT%find-yourself-backend\find-yourself-backend.exe" --host 127.0.0.1 --port 8000 %*
    exit /b %ERRORLEVEL%
)

set PYTHON_CMD=
if exist "%ROOT%\.venv\Scripts\python.exe" (
    set PYTHON_CMD="%ROOT%\.venv\Scripts\python.exe"
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
