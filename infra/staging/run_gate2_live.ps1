param(
    [Parameter(Mandatory = $true)][string]$Evidence,
    [string]$Python = 'python',
    [string]$PythonDependencies,
    [string]$WorkspaceHost,
    [switch]$ApprovedSyntheticStaging
)
$ErrorActionPreference = 'Stop'
if (-not $ApprovedSyntheticStaging) {
    throw 'Use only an approved Beijing test workspace and synthetic cases.'
}
if (-not $WorkspaceHost) {
    $WorkspaceHost = (Read-Host 'Beijing test API host (without https://)').Trim()
}
if ($WorkspaceHost -notmatch '^(?:ws|llm)-[a-z0-9-]+\.cn-beijing\.maas\.aliyuncs\.com$') {
    throw 'Enter the API host of the dedicated Beijing test workspace.'
}
Write-Host 'Yaya Gate 2 | 3 synthetic books | 63 planned Flash calls | local SQLite'
$secret = Read-Host 'Dedicated Beijing test API Key (hidden)' -AsSecureString
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$names = 'YAYA_AGENT2_API_KEY', 'YAYA_AGENT2_API_URL', 'YAYA_AGENT2_LEDGER_SECRET', 'PYTHONPATH', 'PYTHONDONTWRITEBYTECODE'
$previous = @{}
foreach ($name in $names) {
    $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
try {
    $env:YAYA_AGENT2_API_KEY = [System.Net.NetworkCredential]::new('', $secret).Password
    $env:YAYA_AGENT2_API_URL = "https://$WorkspaceHost/compatible-mode/v1/chat/completions"
    $env:YAYA_AGENT2_LEDGER_SECRET = [guid]::NewGuid().ToString('N')
    $env:PYTHONPATH = (@((Join-Path $root 'backend'), $PythonDependencies, $previous.PYTHONPATH) |
        Where-Object { $_ }) -join [IO.Path]::PathSeparator
    $env:PYTHONDONTWRITEBYTECODE = '1'
    & $Python -X utf8 (Join-Path $PSScriptRoot 'gate2_live_probe.py') --approved-synthetic-staging --evidence $Evidence
    if ($LASTEXITCODE -ne 0) {
        throw "Probe stopped with status $LASTEXITCODE. Check the private evidence if the probe created it."
    }
    Write-Host "Private synthetic evidence: $Evidence"
} finally {
    foreach ($name in $names) {
        [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process')
    }
    $secret.Dispose()
}
