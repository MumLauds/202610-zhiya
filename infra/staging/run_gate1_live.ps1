param(
    [Parameter(Mandatory = $true)][string]$Evidence,
    [string]$Python = 'python',
    [switch]$ApprovedSyntheticStaging
)
$ErrorActionPreference = 'Stop'
if (-not $ApprovedSyntheticStaging) {
    throw 'Use an approved Beijing test workspace for synthetic cases, then pass -ApprovedSyntheticStaging.'
}
$workspaceHost = (Read-Host 'Beijing Staging API Host (without https://)').Trim()
if ($workspaceHost -notmatch '^(?:ws|llm)-[a-z0-9-]+\.cn-beijing\.maas\.aliyuncs\.com$') {
    throw 'Enter the API Host of the dedicated Beijing workspace.'
}
$secret = Read-Host 'Dedicated Staging API key (hidden)' -AsSecureString
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$names = 'YAYA_AGENT2_API_KEY', 'YAYA_AGENT2_API_URL', 'YAYA_AGENT2_LEDGER_SECRET', 'PYTHONPATH'
$previous = @{}
foreach ($name in $names) {
    $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
try {
    $env:YAYA_AGENT2_API_KEY = [System.Net.NetworkCredential]::new('', $secret).Password
    $env:YAYA_AGENT2_API_URL = "https://$workspaceHost/compatible-mode/v1/chat/completions"
    $env:YAYA_AGENT2_LEDGER_SECRET = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
    $backendPath = Join-Path $root 'backend'
    $env:PYTHONPATH = if ($previous.PYTHONPATH) {
        $backendPath + [IO.Path]::PathSeparator + $previous.PYTHONPATH
    } else {
        $backendPath
    }
    & $Python (Join-Path $PSScriptRoot 'gate1_live_probe.py') --approved-synthetic-staging --evidence $Evidence
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        throw "Probe exited with status $code. Any recorded synthetic responses remain in the private evidence file."
    }
    Write-Output "Private synthetic evidence: $Evidence"
} finally {
    foreach ($name in $names) {
        [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process')
    }
    $secret.Dispose()
}
