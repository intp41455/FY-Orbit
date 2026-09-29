[CmdletBinding()]
param(
    [string]$SessionId = '38444848832953602',
    [string]$WorkspaceRoot = 'C:\Users\intpj\AppData\Local\Doubao\User Data\Default\.doubao\agent_mode\workspace',
    [string]$ProjectRoot = 'C:\Users\intpj\Documents\Codex\2026-09-29\agent\outputs\find-yourself'
)

$ErrorActionPreference = 'Stop'
$agentsRoot = Join-Path $WorkspaceRoot (Join-Path ".sessions\$SessionId" 'agents')
$outputRoot = Join-Path $ProjectRoot 'evidence\process-traces'
$stageRoot = Join-Path $ProjectRoot ".runtime\process-traces-staging-$(Get-Date -Format 'yyyyMMddHHmmss')"

if (-not (Test-Path -LiteralPath $agentsRoot)) {
    [pscustomobject]@{ ok = $false; error = 'AGENTS_ROOT_NOT_FOUND'; path = $agentsRoot } | ConvertTo-Json -Compress
    exit 2
}

$map = @(
    [ordered]@{ Label = '00_main';       Id = 'm_0cwpLeS9dFS' }
    [ordered]@{ Label = '01_organizer';  Id = 'o_000c2mapnLx' }
    [ordered]@{ Label = '02_core';       Id = 's_000c2maTtRF' }
    [ordered]@{ Label = '03_temporal';   Id = 's_000c2maT2Qg' }
    [ordered]@{ Label = '04_web';        Id = 's_000c2maTx9O' }
    [ordered]@{ Label = '05_infra-cli';  Id = 's_000c2maT6Yg' }
    [ordered]@{ Label = '06_runtime-api'; Id = 's_000c2maZzU6' }
)

New-Item -ItemType Directory -Force -Path $stageRoot | Out-Null
$copied = New-Object System.Collections.Generic.List[string]

foreach ($entry in $map) {
    $sourceAgent = Join-Path $agentsRoot $entry.Id
    if (-not (Test-Path -LiteralPath $sourceAgent)) {
        [pscustomobject]@{ ok = $false; error = 'AGENT_NOT_FOUND'; agent = $entry.Id; path = $sourceAgent } | ConvertTo-Json -Compress
        exit 2
    }

    $dest = Join-Path $stageRoot $entry.Label
    New-Item -ItemType Directory -Force -Path $dest | Out-Null

    $trajectory = Join-Path $sourceAgent 'system\trajectory.jsonl'
    if (Test-Path -LiteralPath $trajectory) {
        Copy-Item -LiteralPath $trajectory -Destination (Join-Path $dest 'trajectory.jsonl') -Force
        $copied.Add((Join-Path $dest 'trajectory.jsonl'))
    }

    foreach ($name in @('tool-results', 'input')) {
        $sourceDir = Join-Path $sourceAgent $name
        if ($name -eq 'tool-results') { $sourceDir = Join-Path $sourceAgent "system\$name" }
        if (Test-Path -LiteralPath $sourceDir) {
            $targetDir = Join-Path $dest $name
            New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
            Copy-Item -Path (Join-Path $sourceDir '*') -Destination $targetDir -Recurse -Force
            Get-ChildItem -LiteralPath $targetDir -Recurse -File | ForEach-Object { $copied.Add($_.FullName) }
        }
    }
}

$patterns = @(
    @{ Id = 'aws_access_key'; Regex = 'AKIA[0-9A-Z]{16}' }
    @{ Id = 'private_key_pem'; Regex = '-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----' }
    @{ Id = 'bearer_credential'; Regex = '(?i)Bearer\s+[A-Za-z0-9\._\-]{20,}' }
    @{ Id = 'openai_style_key'; Regex = 'sk-[A-Za-z0-9]{20,}' }
    @{ Id = 'assigned_secret'; Regex = '(?i)(api[_-]?key|client[_-]?secret|session[_-]?secret|password|passwd|authorization)\s*[:=]\s*[''"]?[A-Za-z0-9\._\-/+]{16,}' }
)
$allowList = @(
    '0123456789abcdef0123456789abcdef'
    'local-smoke-token'
    'client_secret=settings.oidc_client_secret'
    'PASSWORD:-admin_dev_change_me'
    'PASSWORD:-fy_dev_change_me_not_for_prod'
    'PASSWORD:-minio_dev_change_me_not_for_prod'
    'SESSION_SECRET:-replace-me-with-at-least-32-random-chars'
    "SESSION_SECRET='0123456789abcdef0123456789abcdef"
    "SESSION_SECRET='local-dev-session-secret-that-is-long-enough-123456"
)
function Test-PlaceholderSecret {
    param([string]$Value)
    $normalized = $Value.Trim().Trim('''"')
    $placeholderMarkers = @(
        'change_me',
        'replace-me',
        'replace-with',
        'test-session-secret',
        'local-dev-session-secret',
        'settings.oidc_client_secret',
        '0123456789abcdef0123456789abcdef'
    )
    foreach ($marker in $placeholderMarkers) {
        if ($normalized -like "*$marker*") { return $true }
    }
    return $false
}

$findings = @()

foreach ($path in ($copied | Sort-Object -Unique)) {
    $item = Get-Item -LiteralPath $path
    if ($item.Length -gt 10MB) {
        $findings += [pscustomobject]@{ id = 'file_too_large_to_scan'; path = $path.Substring($stageRoot.Length + 1); size = $item.Length }
        continue
    }
    $text = [IO.File]::ReadAllText($path, [Text.Encoding]::UTF8)
    foreach ($pattern in $patterns) {
        foreach ($m in [regex]::Matches($text, $pattern.Regex)) {
            $value = $m.Value
            if ($allowList -contains $value) { continue }
            if ($pattern.Id -eq 'assigned_secret') {
                $assigned = ($value -split '[:=]', 2)[1].Trim().Trim('''"')
                if ($allowList -contains $assigned) { continue }
                if (Test-PlaceholderSecret -Value $assigned) { continue }
            }
            $lineNo = ($text.Substring(0, $m.Index) -split "`n").Count
            $findings += [pscustomobject]@{
                id = $pattern.Id
                path = $path.Substring($stageRoot.Length + 1)
                line = $lineNo
                matched_prefix = ($value.Substring(0, [Math]::Min(8, $value.Length)) + '...')
            }
        }
    }
}

$fileRecords = @($copied | Sort-Object -Unique | ForEach-Object {
    $full = $_
    $hash = Get-FileHash -Algorithm SHA256 -LiteralPath $full
    $info = Get-Item -LiteralPath $full
    [pscustomobject]@{
        path = $full.Substring($stageRoot.Length + 1)
        size = $info.Length
        sha256 = $hash.Hash.ToLowerInvariant()
    }
})

$manifest = [ordered]@{
    refreshed_at_utc = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    session_id = $SessionId
    source_agents_root = $agentsRoot
    output = 'evidence/process-traces'
    file_count = $fileRecords.Count
    total_bytes = ($fileRecords | Measure-Object -Property size -Sum).Sum
    secret_scan_findings = @($findings)
    files = $fileRecords
}
$manifestJson = $manifest | ConvertTo-Json -Depth 6
[IO.File]::WriteAllText((Join-Path $stageRoot 'MANIFEST.json'), $manifestJson, (New-Object Text.UTF8Encoding($false)))

if ($findings.Count -gt 0) {
    $report = Join-Path $ProjectRoot '.runtime\process-traces-secret-findings.json'
    [IO.File]::WriteAllText($report, $manifestJson, (New-Object Text.UTF8Encoding($false)))
    [pscustomobject]@{ ok = $false; error = 'SECRET_SCAN_FAILED'; findings = $findings.Count; staging = $stageRoot; report = $report } | ConvertTo-Json -Compress
    exit 10
}

New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null
foreach ($entry in $map) {
    $old = Join-Path $outputRoot $entry.Label
    $new = Join-Path $stageRoot $entry.Label
    if (Test-Path -LiteralPath $old) { Remove-Item -LiteralPath $old -Recurse -Force }
    Move-Item -LiteralPath $new -Destination $old
}
Copy-Item -LiteralPath (Join-Path $stageRoot 'MANIFEST.json') -Destination (Join-Path $outputRoot 'MANIFEST.json') -Force
Remove-Item -LiteralPath $stageRoot -Recurse -Force

[pscustomobject]@{
    ok = $true
    output = $outputRoot
    file_count = $fileRecords.Count
    total_bytes = ($fileRecords | Measure-Object -Property size -Sum).Sum
    secret_scan_findings = 0
} | ConvertTo-Json -Compress
