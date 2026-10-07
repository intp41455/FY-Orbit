@echo off
chcp 65001 >nul
echo ==============================================================================
echo  FY Orbit · 星轨 —— Cloudflare Pages 静态前端一键部署脚本
echo ==============================================================================
echo.

cd /d "%~dp0..\..\web"

if not exist "dist\index.html" (
    echo [1/3] 正在构建前端生产包 (npm run build)...
    call npm run build
    if errorlevel 1 (
        echo [错误] 前端构建失败，请检查 npm 环境与依赖！
        pause
        exit /b 1
    )
) else (
    echo [1/3] 检测到已存在 web\dist 生产构建包。
)

echo [2/3] 检查 Cloudflare Wrangler CLI...
call npx --yes wrangler --version
if errorlevel 1 (
    echo [错误] 无法调用 wrangler，请确保已安装 Node.js 与 npm！
    pause
    exit /b 1
)

echo [3/3] 正在发布至 Cloudflare Pages (项目名: fy-orbit)...
call npx --yes wrangler pages deploy dist --project-name fy-orbit

echo.
echo ==============================================================================
echo  [完成] 静态前端已成功提交至 Cloudflare 全球 CDN！
echo  访问地址: https://fy-orbit.pages.dev
echo ==============================================================================
pause
