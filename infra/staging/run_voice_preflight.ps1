param(
    [Parameter(Mandatory = $true)][string]$AudioPath,
    [string]$Python = 'python',
    [switch]$ApprovedSyntheticStaging
)
$ErrorActionPreference = 'Stop'
if (-not $ApprovedSyntheticStaging) {
    throw 'Use authorized synthetic-only voice credentials, then pass -ApprovedSyntheticStaging.'
}
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$audio = [IO.Path]::GetFullPath($AudioPath)
if ([IO.Path]::GetExtension($audio) -ne '.wav' -or
    $audio.StartsWith($root + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or
    (Test-Path -LiteralPath $audio) -or -not (Test-Path -LiteralPath (Split-Path -Parent $audio) -PathType Container)) {
    throw 'Use a new WAV path in an existing private directory outside the checkout.'
}
$workspaceHost = (Read-Host 'Beijing voice test API Host (without https://)').Trim()
if ($workspaceHost -notmatch '^(?:ws|llm)-[a-z0-9-]+\.cn-beijing\.maas\.aliyuncs\.com$') {
    throw 'Enter the API Host of the authorized Beijing voice test workspace.'
}
$secret = Read-Host 'Dedicated voice test API key (hidden)' -AsSecureString
$previous = @{}
$previousOutputEncoding = $OutputEncoding
$config = @{
    DASHSCOPE_API_KEY = [System.Net.NetworkCredential]::new('', $secret).Password
    ASR_API_URL = "https://$workspaceHost/compatible-mode/v1/chat/completions"
    TTS_API_URL = "https://$workspaceHost/api/v1/services/audio/tts/SpeechSynthesizer"
    ASR_MODEL = 'qwen3-asr-flash'
    TTS_MODEL = 'qwen-audio-3.1-tts-flash'
    TTS_VOICE = 'wenhuaiqing_v3.1'
    VOICE_TIMEOUT_SECONDS = '20'
}
try {
    if (-not $config.DASHSCOPE_API_KEY -or $config.DASHSCOPE_API_KEY -in @($env:DASHSCOPE_API_KEY, $env:YAYA_AGENT2_API_KEY)) {
        throw 'Use a separate voice test key.'
    }
    foreach ($name in $config.Keys) {
        $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, $config[$name], 'Process')
    }
    $probe = Join-Path $root 'backend\smoke_voice.py'
    $OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    '小熊戴着蓝色的帽子。' | & $Python -X utf8 $probe tts $audio
    if ($LASTEXITCODE -ne 0) { throw 'TTS failed. No ASR request was sent.' }
    & $Python -X utf8 $probe asr $audio --format wav
    if ($LASTEXITCODE -ne 0) { throw 'ASR failed. The synthetic TTS WAV remains available.' }
    Write-Output "Private synthetic voice sample: $audio"
} finally {
    foreach ($name in $previous.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process')
    }
    $OutputEncoding = $previousOutputEncoding
    $secret.Dispose()
}
