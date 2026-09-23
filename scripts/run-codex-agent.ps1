$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $projectRoot '.env'
if (-not (Test-Path -LiteralPath $envPath)) {
    throw 'Missing project .env. Copy .env to .env and set OPENAI_API_KEY.'
}

$settings = @{}
foreach ($line in Get-Content -LiteralPath $envPath) {
    if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$') {
        $settings[$Matches[1]] = $Matches[2].Trim().Trim('"').Trim("'")
    }
}

$apiKey = $settings['OPENAI_API_KEY']
if ([string]::IsNullOrWhiteSpace($apiKey)) {
    throw 'Set OPENAI_API_KEY in the project .env file before starting Codex.'
}
if ([string]::IsNullOrWhiteSpace($settings['CODEX_REMOTE_URL']) -or
    [string]::IsNullOrWhiteSpace($settings['CODEX_ENVIRONMENT_ID'])) {
    throw 'Set CODEX_REMOTE_URL and CODEX_ENVIRONMENT_ID in the project .env file.'
}

$env:CODEX_API_KEY = $apiKey
& codex exec-server `
    --remote $settings['CODEX_REMOTE_URL'] `
    --environment-id $settings['CODEX_ENVIRONMENT_ID']
exit $LASTEXITCODE
