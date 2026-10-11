# Verify every packaged file. Data created after first launch is outside this manifest.
$ErrorActionPreference = 'Stop'
try {
    $basePath = [IO.Path]::GetFullPath($PSScriptRoot)
    $manifest = Join-Path $basePath 'SHA256SUMS.txt'
    if (-not (Test-Path -LiteralPath $manifest -PathType Leaf)) { throw 'Missing SHA256SUMS.txt' }
    $count = 0
    foreach ($line in [IO.File]::ReadAllLines($manifest, [Text.Encoding]::UTF8)) {
        if ($line.StartsWith('#') -or [string]::IsNullOrWhiteSpace($line)) { continue }
        if ($line -notmatch '^([0-9a-fA-F]{64})  (.+)$') { throw 'Invalid checksum line' }
        $expected = $Matches[1]
        $relative = $Matches[2]
        if ([IO.Path]::IsPathRooted($relative)) { throw 'Absolute manifest path is forbidden' }
        $target = [IO.Path]::GetFullPath((Join-Path $basePath $relative))
        if (-not $target.StartsWith($basePath + [IO.Path]::DirectorySeparatorChar,
                                    [StringComparison]::OrdinalIgnoreCase)) { throw 'Manifest path escapes package' }
        if (-not (Test-Path -LiteralPath $target -PathType Leaf)) { throw "Missing package file: $relative" }
        $algorithm = [Security.Cryptography.SHA256]::Create()
        $stream = [IO.File]::OpenRead($target)
        try {
            $actual = [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-', '')
        } finally {
            $stream.Dispose()
            $algorithm.Dispose()
        }
        if ($actual -ne $expected) { throw "Checksum mismatch: $relative" }
        $count++
    }
    if ($count -eq 0) { throw 'Empty checksum manifest' }
    Write-Output "Verified $count package files."
    exit 0
} catch {
    Write-Error $_
    exit 1
}
