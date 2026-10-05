[CmdletBinding()]
param(
    [string]$ProjectName = "find-yourself",
    [string]$Branch = "feat/package-a",
    [string]$ApiBaseUrl = ""
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$RepoRoot = Split-Path -Parent $ScriptDir
$WebDir = Join-Path $RepoRoot "web"

Write-Host "==> [1/3] Building Web frontend..." -ForegroundColor Cyan
Push-Location $WebDir
try {
    if ($ApiBaseUrl) {
        $env:VITE_API_BASE_URL = $ApiBaseUrl
        Write-Host "    Injected VITE_API_BASE_URL: $ApiBaseUrl"
    }
    npm run build
    if ($LASTEXITCODE -ne 0) {
        throw "Frontend build failed with exit code $LASTEXITCODE"
    }

    Write-Host "==> [2/3] Checking dist output..." -ForegroundColor Cyan
    $DistDir = Join-Path $WebDir "dist"
    if (-not (Test-Path $DistDir)) {
        throw "dist directory not found at $DistDir"
    }

    Write-Host "==> [3/3] Deploying to Cloudflare Pages via Wrangler..." -ForegroundColor Cyan
    npx wrangler pages deploy dist --project-name $ProjectName --branch $Branch
    if ($LASTEXITCODE -ne 0) {
        throw "Wrangler deploy failed with exit code $LASTEXITCODE"
    }
    Write-Host "==> Deployment completed successfully!" -ForegroundColor Green
}
finally {
    Pop-Location
}
