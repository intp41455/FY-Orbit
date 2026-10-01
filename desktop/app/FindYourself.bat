@echo off
title Find Yourself Desktop
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo 启动发生错误，按任意键退出...
    pause >nul
)
