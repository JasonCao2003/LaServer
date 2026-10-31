param([switch]$Setup)
$ErrorActionPreference = 'Stop'
$serviceDirectory = $PSScriptRoot
$projectDirectory = [IO.Path]::GetFullPath((Join-Path $serviceDirectory '../..'))
. (Join-Path $projectDirectory 'scripts/load-env.ps1')
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw 'Missing uv' }
Push-Location -LiteralPath $projectDirectory
try {
    if ($Setup) {
        foreach ($pair in @(
            @('.env.example', '.env'),
            @('config/server.example.json', 'config/server.json'),
            @('config/room_config.example.json', 'config/room_config.json')
        )) {
            $destination = Join-Path $serviceDirectory $pair[1]
            if (-not (Test-Path -LiteralPath $destination)) {
                Copy-Item -LiteralPath (Join-Path $serviceDirectory $pair[0]) -Destination $destination
            }
        }
        & uv sync --locked
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        Write-Host 'Environment prepared. Review .env, configuration, and var/certs before starting.'
    } else {
        Import-ServiceEnvironment (Join-Path $serviceDirectory '.env')
        foreach ($relative in @('config/server.json', 'config/room_config.json', 'var/certs/fullchain.pem', 'var/certs/privkey.pem')) {
            if (-not (Test-Path -LiteralPath (Join-Path $serviceDirectory $relative))) { throw "Missing service file: $relative" }
        }
        & uv run --locked python -u (Join-Path $serviceDirectory 'sky_boot.py')
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
} finally {
    Pop-Location
}
