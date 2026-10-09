<#
.SYNOPSIS
    等待依赖就绪：TCP 端口可连，或 HTTP 路径返回 <500（Infra 运维脚本）。

.DESCRIPTION
    用于分阶段启动时等待 Postgres / Temporal / MinIO / API 就绪，
    避免应用层在数据层未起时崩溃重试。只探活，不执行业务写操作。

.PARAMETER Host
    目标主机（默认 127.0.0.1）。

.PARAMETER Port
    目标 TCP 端口；与 -Url 二选一。

.PARAMETER Url
    目标 HTTP 地址；用 HTTP 探活时传入。

.PARAMETER TimeoutSec
    最长等待秒数，默认 60。

.PARAMETER IntervalSec
    轮询间隔，默认 2。

.EXAMPLE
    pwsh scripts/wait-for.ps1 -Host 127.0.0.1 -Port 5432 -TimeoutSec 60
    pwsh scripts/wait-for.ps1 -Url http://127.0.0.1:8000/health/live -TimeoutSec 30

.NOTES
    退出码：0=就绪；3=超时仍未就绪（NOT_RUN）。
#>
[CmdletBinding()]
param(
    [string]$Host = "127.0.0.1",
    [int]$Port = 0,
    [string]$Url = "",
    [int]$TimeoutSec = 60,
    [int]$IntervalSec = 2
)

$deadline = (Get-Date).AddSeconds($TimeoutSec)
function Test-Ready {
    if ($Url) {
        try {
            $r = Invoke-WebRequest -Uri $Url -TimeoutSec 3 -UseBasicParsing -ErrorAction Stop
            return $r.StatusCode -lt 500
        } catch { return $false }
    } else {
        try {
            $client = New-Object System.Net.Sockets.TcpClient
            $iar = $client.BeginConnect($Host, $Port, $null, $null)
            $ok = $iar.AsyncWaitHandle.WaitOne(2000)
            if ($ok -and $client.Connected) { $client.EndConnect($iar); $client.Close(); return $true }
            $client.Close(); return $false
        } catch { return $false }
    }
}

while ((Get-Date) -lt $deadline) {
    if (Test-Ready) {
        [ordered]@{ command="wait-for.ps1"; target=($(if($Url){$Url}else{"${Host}:${Port}"})); status="OK"; exit_code=0 } |
            ConvertTo-Json
        exit 0
    }
    Start-Sleep -Seconds $IntervalSec
}
[ordered]@{ command="wait-for.ps1"; target=($(if($Url){$Url}else{"${Host}:${Port}"})); status="NOT_RUN"; exit_code=3 } |
    ConvertTo-Json
exit 3
