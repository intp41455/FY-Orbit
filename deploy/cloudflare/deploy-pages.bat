@echo off
chcp 65001 >nul
echo ==============================================================================
echo  FY Orbit - Landing Page (fy-orbit.pages.dev) Deploy
echo ==============================================================================
echo.

cd /d "%~dp0"

if not exist "dist\index.html" (
    echo [ERROR] dist\index.html not found. This folder must contain the landing site.
    pause
    exit /b 1
)

echo [1/2] Checking Cloudflare Wrangler CLI...
call npx --yes wrangler --version
if errorlevel 1 (
    echo [ERROR] wrangler unavailable. Please install Node.js and npm first.
    pause
    exit /b 1
)

echo [2/2] Deploying landing page to Cloudflare Pages (project: fy-orbit)...
call npx --yes wrangler pages deploy dist --project-name fy-orbit

echo.
echo ==============================================================================
echo  [DONE] Landing page published to Cloudflare global CDN.
echo  URL: https://fy-orbit.pages.dev
echo ==============================================================================
pause
