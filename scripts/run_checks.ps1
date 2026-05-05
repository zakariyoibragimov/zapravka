param(
    [ValidateSet('smoke', 'core', 'full')]
    [string]$Suite = 'core'
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    throw 'Virtual environment .venv was not found. Create it and install dependencies first.'
}

$suites = @{
    smoke = @(
        'tests/test_bonus_expiry.py',
        'tests/test_admin_settings_api.py',
        'tests/test_reports_api.py'
    )
    core = @(
        'tests/test_bonus_expiry.py',
        'tests/test_admin_settings_api.py',
        'tests/test_api_cash.py',
        'tests/test_mobile_api.py',
        'tests/test_reports_api.py',
        'tests/test_admin_suspicious_api.py',
        'tests/test_news_api.py'
    )
    full = @('tests')
}

$targets = $suites[$Suite]
Write-Host "Running suite: $Suite" -ForegroundColor Cyan
Write-Host ($targets -join "`n")

& $python -m pytest @targets --color=no
exit $LASTEXITCODE