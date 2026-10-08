@echo off
chcp 65001 >nul
echo ==============================================================================
echo  FY Orbit - Web Workbench SPA (find-yourself) Deploy
echo ==============================================================================
echo.
echo  NOTE: This deploys the WEB APP (SPA), NOT the landing page.
echo        Landing page  -> deploy-pages.bat  -> project: fy-orbit
echo        This script   -> project: find-yourself (find-yourself-45j.pages.dev)
echo.

cd /d "%~dp0..\..\web"

if not exist "dist\index.html" (
    echo [1/3] Building frontend production bundle (npm run build)...
    call npm run build
    if errorlevel 1 (
        echo [ERROR] Frontend build failed. Check Node.js / npm environment.
        pause
        exit /b 1
    )
) else (
    echo [1/3] Found existing web\dist build output.
)

echo [2/3] Checking Cloudflare Wrangler CLI...
call npx --yes wrangler --version
if errorlevel 1 (
    echo [ERROR] wrangler unavailable. Please install Node.js and npm first.
    pause
    exit /b 1
)

echo [3/3] Deploying SPA to Cloudflare Pages (project: find-yourself)...
call npx --yes wrangler pages deploy dist --project-name find-yourself

echo.
echo ==============================================================================
echo  [DONE] Web workbench published. URL: https://find-yourself-45j.pages.dev
echo ==============================================================================
pause
