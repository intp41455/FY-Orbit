<#
.SYNOPSIS
  Find Yourself 一键验收脚本（分模块交付证据）。

.DESCRIPTION
  按模块依次执行并把原始输出写入 evidence/acceptance-<run>/：

    TEAM-UNIT      19 号团队能力单测（独立于既有 354 基线）
    TEAM-API       19 号团队能力 API 测试
    MIGRATION      迁移 0005 upgrade / downgrade / upgrade
    WEB-TYPE       前端 tsc --noEmit
    WEB-UNIT       前端 vitest
    WEB-BUILD      前端 vite build
    BACKEND        全量后端回归
    CONCURRENCY    并发回归（-Concurrency，真实并发压测工作台端点）
    TEAM-E2E       新界面端到端（需先起后端与 preview，见 -E2E）

  每个模块独立退出码；任何一个失败都不会被吞掉，也不会被后续模块覆盖。
  不使用管理员权限，不触碰工作树既有改动，不自动提交，不写入任何凭据。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\acceptance.ps1
  powershell -ExecutionPolicy Bypass -File scripts\acceptance.ps1 -Modules TEAM-UNIT,WEB-TYPE
  powershell -ExecutionPolicy Bypass -File scripts\acceptance.ps1 -Concurrency -ApiBase http://127.0.0.1:8030
#>
[CmdletBinding()]
param(
  [string[]]$Modules = @(
    'TEAM-UNIT', 'TEAM-API', 'MIGRATION', 'WEB-TYPE', 'WEB-UNIT',
    'WEB-BUILD', 'BACKEND', 'CONCURRENCY'
  ),
  # 隔离的验收数据库；绝不指向开发库。
  [string]$DatabaseUrl = '',
  # 并发回归参数
  [switch]$Concurrency,
  [string]$ApiBase = 'http://127.0.0.1:8030',
  [int]$ConcurrencyRequests = 60,
  [int]$ConcurrencyWorkers = 12,
  # E2E 参数（需要 E2E_API_TARGET / E2E_LOCAL_TOKEN 环境变量）
  [switch]$E2E,
  [string]$RunTag = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

if ([string]::IsNullOrWhiteSpace($RunTag)) { $RunTag = (Get-Date -Format 'yyyy-MM-dd-HHmmss') }
$EvidenceDir = Join-Path $RepoRoot "evidence\acceptance-$RunTag"
New-Item -ItemType Directory -Force -Path $EvidenceDir | Out-Null

if ([string]::IsNullOrWhiteSpace($DatabaseUrl)) {
  $dbPath = Join-Path $RepoRoot ".runtime\acceptance-$RunTag.db"
  New-Item -ItemType Directory -Force -Path (Join-Path $RepoRoot '.runtime') | Out-Null
  $DatabaseUrl = "sqlite:///$($dbPath -replace '\\','/')"
}

# 只在本进程内设置；不写入任何文件，不回显密钥。
$env:FY_SESSION_SECRET = $env:FY_SESSION_SECRET
if ([string]::IsNullOrWhiteSpace($env:FY_SESSION_SECRET)) {
  $env:FY_SESSION_SECRET = 'acceptance-local-secret-not-persisted-0123456789'
}
$env:FY_DATABASE_URL = $DatabaseUrl

$results = [System.Collections.Generic.List[object]]::new()

function Write-Log([string]$Name, [string]$Text) {
  $path = Join-Path $EvidenceDir "$Name.log"
  Set-Content -Path $path -Value $Text -Encoding utf8
  return $path
}

function Invoke-Module {
  param(
    [Parameter(Mandatory)][string]$Name,
    [Parameter(Mandatory)][string]$WorkingDir,
    [Parameter(Mandatory)][scriptblock]$Action,
    [int[]]$AllowedExitCodes = @(0)
  )

  Write-Host ""
  Write-Host "=== [$Name] ===" -ForegroundColor Cyan
  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  $global:LASTEXITCODE = 0
  Push-Location $WorkingDir
  try {
    $out = & $Action 2>&1 | Out-String
    $code = $global:LASTEXITCODE
  } catch {
    $out = $_.Exception.Message
    $code = 1
  } finally {
    Pop-Location
    $sw.Stop()
  }

  Write-Output $out
  $logPath = Write-Log $Name $out
  $ok = $AllowedExitCodes -contains $code
  $status = if ($ok) { 'PASS' } else { 'FAIL' }
  $results.Add([pscustomobject]@{
    Module   = $Name
    Status   = $status
    ExitCode = $code
    Seconds  = [math]::Round($sw.Elapsed.TotalSeconds, 1)
    Log      = (Resolve-Path -Relative $logPath)
  }) | Out-Null

  $colour = if ($ok) { 'Green' } else { 'Red' }
  Write-Host ("[{0}] {1} exit={2} {3}s -> {4}" -f $status, $Name, $code, [math]::Round($sw.Elapsed.TotalSeconds,1), $logPath) -ForegroundColor $colour
}

$root = $RepoRoot
$web = Join-Path $RepoRoot 'web'

foreach ($m in $Modules) {
  switch ($m) {
    'TEAM-UNIT' {
      Invoke-Module -Name 'team-unit' -WorkingDir $root -Action {
        uv run --no-sync pytest tests/unit/test_agent_teams.py -q -rs
      }
    }
    'TEAM-API' {
      Invoke-Module -Name 'team-api' -WorkingDir $root -Action {
        uv run --no-sync pytest tests/api/test_agent_teams_api.py -q -rs
      }
    }
    'MIGRATION' {
      Invoke-Module -Name 'migration-0005' -WorkingDir $root -Action {
        $script = @'
import os, sqlite3, subprocess, sys
db = os.environ["FY_DATABASE_URL"].replace("sqlite:///", "")
def alembic(*args):
    r = subprocess.run([sys.executable, "-m", "alembic", *args],
                       capture_output=True, text=True)
    print(r.stdout[-2000:], r.stderr[-2000:])
    return r.returncode
def tables():
    c = sqlite3.connect(db)
    return {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
want = {"team_definitions","model_bindings","agent_instances",
        "agent_control_capabilities","control_requests","team_events"}
rc = 0
if os.path.exists(db): os.remove(db)
rc |= alembic("upgrade", "head")
missing = want - tables()
print("missing_after_upgrade:", sorted(missing)); rc |= 1 if missing else 0
rc |= alembic("downgrade", "0004_workbench")
left = {t for t in tables() if t in want}
print("left_after_downgrade:", sorted(left)); rc |= 1 if left else 0
rc |= alembic("upgrade", "head")
again = want - tables()
print("missing_after_reupgrade:", sorted(again)); rc |= 1 if again else 0
rc |= alembic("current")
print("migration_rc:", rc)
sys.exit(rc)
'@
        $script | uv run --no-sync python -
      }
    }
    'WEB-TYPE' {
      Invoke-Module -Name 'web-typecheck' -WorkingDir $web -Action { npm run typecheck }
    }
    'WEB-UNIT' {
      Invoke-Module -Name 'web-vitest' -WorkingDir $web -Action { npm test }
    }
    'WEB-BUILD' {
      Invoke-Module -Name 'web-build' -WorkingDir $web -Action { npm run build }
    }
    'BACKEND' {
      Invoke-Module -Name 'backend-full' -WorkingDir $root -Action {
        uv run --no-sync pytest tests/unit tests/api tests/integration -q -rs
      }
    }
    'CONCURRENCY' {
      Invoke-Module -Name 'concurrency' -WorkingDir $root -Action {
        $env:FY_CONCURRENCY_API = $ApiBase
        $env:FY_CONCURRENCY_REQUESTS = "$ConcurrencyRequests"
        $env:FY_CONCURRENCY_WORKERS = "$ConcurrencyWorkers"
        uv run --no-sync python scripts/concurrency_regression.py
      }
    }
    'TEAM-E2E' {
      if (-not $E2E) {
        Write-Host '[-] TEAM-E2E 需要 -E2E 开关（会启动 preview 并要求 E2E_LOCAL_TOKEN）。跳过。' -ForegroundColor Yellow
      } else {
        Invoke-Module -Name 'team-e2e' -WorkingDir $web -Action { npm run test:e2e -- ui-team.spec.ts }
      }
    }
    default {
      Write-Host "[!] 未知模块: $m" -ForegroundColor Red
      $results.Add([pscustomobject]@{
        Module = $m; Status = 'FAIL'; ExitCode = 2; Seconds = 0; Log = ''
      }) | Out-Null
    }
  }
}

# ---------------------------------------------------------------- summary
Write-Host ""
Write-Host "================ 验收汇总 ($RunTag) ================" -ForegroundColor Cyan
$results | Format-Table -AutoSize | Out-String | Write-Host

$table = $results | ConvertTo-Json -Depth 4
Set-Content -Path (Join-Path $EvidenceDir 'summary.json') -Value $table -Encoding utf8

$md = @()
$md += "# 验收汇总 $RunTag"
$md += ''
$md += '| 模块 | 状态 | 退出码 | 耗时(s) | 日志 |'
$md += '| --- | --- | --- | --- | --- |'
foreach ($r in $results) {
  $md += "| $($r.Module) | $($r.Status) | $($r.ExitCode) | $($r.Seconds) | ``$($r.Log)`` |"
}
$md += ''
$md += "数据库（隔离，未指向开发库）：``$DatabaseUrl``"
Set-Content -Path (Join-Path $EvidenceDir 'SUMMARY.md') -Value ($md -join "`n") -Encoding utf8

$failed = @($results | Where-Object { $_.Status -eq 'FAIL' })
Write-Host ("证据目录: {0}" -f $EvidenceDir)
if ($failed.Count -gt 0) {
  Write-Host ("失败模块 {0} 个：{1}" -f $failed.Count, (($failed | ForEach-Object { $_.Module }) -join ', ')) -ForegroundColor Red
  exit 1
}
Write-Host '全部所选模块通过。' -ForegroundColor Green
exit 0
