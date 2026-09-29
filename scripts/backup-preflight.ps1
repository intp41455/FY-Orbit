<#
.SYNOPSIS
    备份/恢复预检（Infra 运维脚本）。

.DESCRIPTION
    在真正执行 pg_dump / 对象镜像之前检查前提条件：
      1. docker / docker compose 可用；
      2. 数据层容器是否处于 healthy（未起则 NOT_RUN，不伪造 dump）；
      3. 备份目标目录可写；
      4. 配置中未要求创建 public bucket。
    本脚本只做预检与报告，不执行任何备份/恢复写操作。

.PARAMETER ComposeFile
    默认 infra/compose.dev.yml。

.PARAMETER TargetDir
    待检查的备份目的地目录。

.EXAMPLE
    pwsh scripts/backup-preflight.ps1 -TargetDir .runtime/backup-test

.NOTES
    退出码：0=预检通过；3=服务未就绪(NOT_RUN)；4=安全守卫不通过(如要求 public bucket)。
#>
[CmdletBinding()]
param(
    [string]$ComposeFile = "infra/compose.dev.yml",
    [string]$TargetDir = ".runtime/backup-test"
)

$checks = @()

$docker = $null
try { $docker = (docker version --format '{{.Server.Version}}' 2>$null) } catch { $docker = $null }
$checks += [pscustomobject]@{ name="docker_server"; status=$(if($docker){"OK"}else{"NOT_RUN"}); detail=$docker }

if ($docker) {
    try {
        $svc = docker compose -f $ComposeFile ps --format json 2>$null | ConvertFrom-Json
        $pg = $svc | Where-Object { $_.Service -eq "postgres" }
        $checks += [pscustomobject]@{ name="postgres_container";
            status=$(if($pg -and $pg.Health -like "*healthy*"){"OK"}else{"NOT_RUN"});
            detail=($(if($pg){$pg.Health}else{"absent"})) }
    } catch {
        $checks += [pscustomobject]@{ name="postgres_container"; status="NOT_RUN"; detail="compose ps failed" }
    }
}

$dirOk = $true
try {
    New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null
    $probe = Join-Path $TargetDir (".write-" + [guid]::NewGuid().ToString("N") + ".tmp")
    Set-Content -Path $probe -Value "ok" -Encoding utf8
    Remove-Item $probe -Force
} catch { $dirOk = $false }
$checks += [pscustomobject]@{ name="target_writable"; status=$(if($dirOk){"OK"}else{"FAIL"}); detail=$TargetDir }

# 安全守卫：绝不允许 public 访问策略
$pub = $env:FY_S3_PUBLIC_POLICY
$checks += [pscustomobject]@{ name="s3_public_policy";
    status=$(if($pub -eq "public"){"REFUSED"}else{"OK"});
    detail=$(if($pub -eq "public"){"public bucket forbidden"}else{"private only"}) }

$status = if ($checks.status -contains "REFUSED") { "REFUSED"; $code=4 }
           elseif ($checks.status -contains "FAIL") { "FAIL"; $code=2 }
           elseif (($checks.status -notcontains "OK") -or ($checks.status -contains "NOT_RUN")) { "NOT_RUN"; $code=3 }
           else { "OK"; $code=0 }

[ordered]@{
    command="backup-preflight.ps1"
    recorded_at_utc=(Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    status=$status; exit_code=$code; checks=$checks
} | ConvertTo-Json -Depth 5
exit $code
