<#
.SYNOPSIS
    Find Yourself API 健康探针（Infra 运维脚本）。

.DESCRIPTION
    探测本地 API 的 /health/live 与 /health/ready。
    本脚本只做 HTTP 探活，不读取、不回显任何业务数据或密钥。
    输出 JSON 到 stdout，适合被编排脚本消费。

.PARAMETER BaseUrl
    API 根地址，默认 http://127.0.0.1:8000（只读回环）。

.PARAMETER TimeoutSec
    单次请求超时秒数，默认 3。

.EXAMPLE
    pwsh scripts/healthcheck.ps1
    pwsh scripts/healthcheck.ps1 -BaseUrl http://127.0.0.1:8000 -TimeoutSec 5

.NOTES
    退出码：0=live&ready 均通过；2=至少一项未通过；3=服务未就绪(连接被拒)。
    该脚本不伪造“服务在线”：连接失败如实返回 NOT_RUN。
#>
[CmdletBinding()]
param(
    [string]$BaseUrl = "http://127.0.0.1:8000",
    [int]$TimeoutSec = 3
)

function Probe([string]$path) {
    try {
        $resp = Invoke-WebRequest -Uri ($BaseUrl.TrimEnd('/') + $path) `
            -TimeoutSec $TimeoutSec -UseBasicParsing -ErrorAction Stop
        return @{ path = $path; status = "OK"; code = [int]$resp.StatusCode }
    } catch {
        $code = $null
        if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
        return @{ path = $path; status = "NOT_RUN"; code = $code; detail = $_.Exception.GetType().Name }
    }
}

$live  = Probe "/health/live"
$ready = Probe "/health/ready"
$result = [ordered]@{
    command = "healthcheck.ps1"
    recorded_at_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    live  = $live
    ready = $ready
}

if ($live.status -ne "OK" -or $ready.status -ne "OK") {
    $result.status = if ($live.code -or $ready.code) { "DEGRADED" } else { "NOT_RUN" }
    $result.exit_code = if ($live.code -or $ready.code) { 2 } else { 3 }
} else {
    $result.status = "OK"
    $result.exit_code = 0
}

$result | ConvertTo-Json -Depth 5
exit $result.exit_code
