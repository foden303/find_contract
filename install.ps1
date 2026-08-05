# Quick installer for Windows (PowerShell 5.1+).
#
#   .\install.ps1            # Docker if available, otherwise a local venv
#   .\install.ps1 docker     # force Docker + self-hosted SearXNG
#   .\install.ps1 native     # force a local Python venv
#
# If Windows blocks the script:
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

param([ValidateSet('auto', 'docker', 'native')][string]$Mode = 'auto')

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

function Step($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host " OK $m" -ForegroundColor Green }
function Warn($m) { Write-Host " !  $m" -ForegroundColor Red }
function Have($c) { return [bool](Get-Command $c -ErrorAction SilentlyContinue) }

$Port = if ($env:FINDER_PORT) { $env:FINDER_PORT } else { '8765' }

function Invoke-Compose {
    param([Parameter(ValueFromRemainingArguments = $true)]$Args)
    & docker compose @Args
    return $LASTEXITCODE
}

function Test-Docker {
    if (-not (Have 'docker')) { return $false }
    docker info 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { return $false }
    docker compose version 2>&1 | Out-Null
    return $LASTEXITCODE -eq 0
}

if ($Mode -eq 'auto') { $Mode = if (Test-Docker) { 'docker' } else { 'native' } }

# --- Docker ------------------------------------------------------------------

if ($Mode -eq 'docker') {
    Step 'Installing with Docker (app + self-hosted SearXNG)'

    if (-not (Have 'docker')) {
        Warn 'Docker is not installed. Get Docker Desktop: https://docs.docker.com/desktop/install/windows-install/'
        exit 1
    }
    docker info 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Warn 'Docker is installed but not running. Start Docker Desktop and retry.'; exit 1 }

    if (-not (Test-Path '.env')) {
        Step 'Generating .env with a fresh SearXNG secret'
        $bytes = New-Object byte[] 32
        [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
        $secret = ($bytes | ForEach-Object { $_.ToString('x2') }) -join ''
        # ASCII without BOM: docker compose does not parse a UTF-8 BOM
        [IO.File]::WriteAllText("$PSScriptRoot\.env",
            "SEARXNG_SECRET=$secret`nFINDER_PORT=$Port`n",
            (New-Object Text.ASCIIEncoding))
        Ok 'Wrote .env'
    } else {
        Ok '.env already exists - leaving it alone'
    }

    Step 'Building and starting containers (first run pulls images, a few minutes)'
    Invoke-Compose up -d --build | Out-Null
    if ($LASTEXITCODE -ne 0) { Warn 'docker compose failed. Check: docker compose logs'; exit 1 }

    Step 'Waiting for the app to answer'
    foreach ($i in 1..60) {
        try {
            Invoke-WebRequest "http://127.0.0.1:$Port/" -UseBasicParsing -TimeoutSec 3 | Out-Null
            Ok 'Ready'
            Write-Host ''
            Write-Host "  Open http://127.0.0.1:$Port" -ForegroundColor White
            Write-Host '  Search backend: self-hosted SearXNG (no API key)' -ForegroundColor DarkGray
            Write-Host ''
            Write-Host '  Logs:  docker compose logs -f'
            Write-Host '  Stop:  docker compose down'
            exit 0
        } catch { Start-Sleep -Seconds 2 }
    }

    Warn 'The app did not come up in time. Check: docker compose logs'
    exit 1
}

# --- Native venv -------------------------------------------------------------

Step 'Installing natively (Python venv, DuckDuckGo search)'

$py = $null
foreach ($cand in @('python', 'python3', 'py')) {
    if (-not (Have $cand)) { continue }
    $v = & $cand -c 'import sys; print(1 if sys.version_info >= (3,10) else 0)' 2>$null
    if ($v -eq '1') { $py = $cand; break }
}

if (-not $py) {
    Warn 'Python 3.10+ not found.'
    Write-Host '  Install it with:  winget install Python.Python.3.12'
    Write-Host '  or download from https://www.python.org/downloads/'
    exit 1
}
Ok "Using $(& $py --version)"

if (-not (Test-Path 'venv')) { & $py -m venv venv }
& .\venv\Scripts\python.exe -m pip install -q --upgrade pip
Step 'Installing dependencies'
& .\venv\Scripts\python.exe -m pip install -q -r requirements.txt
if ($LASTEXITCODE -ne 0) { Warn 'Dependency install failed'; exit 1 }
Ok 'Dependencies installed'

& .\venv\Scripts\python.exe -c 'import core.pipeline, web.app'
if ($LASTEXITCODE -ne 0) { Warn 'Import check failed'; exit 1 }

Write-Host ''
Write-Host '  Start it with:  .\venv\Scripts\python.exe -m web.app' -ForegroundColor White
Write-Host "  Then open http://127.0.0.1:$Port"
Write-Host ''
Write-Host '  This uses DuckDuckGo, which throttles at ~6 parallel searches.' -ForegroundColor DarkGray
Write-Host '  For large files, run .\install.ps1 docker to get SearXNG as well.' -ForegroundColor DarkGray
