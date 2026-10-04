# Probity - start everything for local development (Windows PowerShell).
#
#   powershell -ExecutionPolicy Bypass -File scripts\dev.ps1            # start
#   powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 -Reset     # start with a fresh demo database
#
# Opens three windows (API, worker, web). Close those windows to stop; `docker compose -f infra\docker-compose.dev.yml down`
# stops Postgres/Redis.
param([switch]$Reset)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$api = Join-Path $root "apps\api"
$web = Join-Path $root "apps\web"
$py = Join-Path $root ".venv\Scripts\python.exe"

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

Step "Checking prerequisites"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw "Docker is not installed. Install Docker Desktop and start it." }
docker info *> $null
if ($LASTEXITCODE -ne 0) { throw "Docker Desktop is not running. Start it and run this script again." }
if (-not (Test-Path $py)) {
    Step "Creating Python environment (first run only)"
    python -m venv (Join-Path $root ".venv")
    & $py -m pip install --upgrade pip
    & $py -m pip install -e "$api[dev,live]"
}
if (-not (Test-Path (Join-Path $web "node_modules"))) {
    Step "Installing web dependencies (first run only)"
    Push-Location $web; npm install; Pop-Location
}
if (-not (Test-Path (Join-Path $root ".env"))) {
    Step "Creating .env for local development"
    @"
ENV=dev
DATABASE_URL=postgresql+psycopg://probity_app:probity_app@localhost:5434/probity
DATABASE_MIGRATE_URL=postgresql+psycopg://probity:probity@localhost:5434/probity
REDIS_URL=redis://localhost:6380/0
TASK_BACKEND=celery
TOOLS_MODE=cached
LLM_MODE=mock
AUTH_MODE=local
AGENT_DELAY_MS=350
CORS_ORIGINS=http://localhost:5180,http://127.0.0.1:5180
"@ | Set-Content -Encoding utf8 (Join-Path $root ".env")
}

Step "Starting Postgres and Redis (Docker)"
docker compose -f (Join-Path $root "infra\docker-compose.dev.yml") up -d --wait
if ($LASTEXITCODE -ne 0) { throw "Docker services failed to start." }

Step "Applying database migrations"
Push-Location $api
& $py -m probity.db.migrate upgrade
if ($Reset) {
    Step "Resetting demo data"
    & $py -m probity.demo.seed --reset
} else {
    & $py -m probity.demo.seed
}
Pop-Location

Step "Starting API, worker and web in their own windows"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "`$Host.UI.RawUI.WindowTitle='Probity API'; Set-Location '$api'; & '$py' -m uvicorn probity.api.main:app --host 127.0.0.1 --port 8010 --reload"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "`$Host.UI.RawUI.WindowTitle='Probity worker'; Set-Location '$api'; & '$py' -m celery -A probity.worker worker --pool=solo -Q probity --loglevel=INFO"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "`$Host.UI.RawUI.WindowTitle='Probity web'; Set-Location '$web'; npm run dev"

Step "Waiting for the API"
$ok = $false
for ($i = 0; $i -lt 40; $i++) {
    try { $r = Invoke-RestMethod -Uri "http://127.0.0.1:8010/api/v1/ready" -TimeoutSec 2; if ($r.ok) { $ok = $true; break } } catch { }
    Start-Sleep -Seconds 1
}
if ($ok) { Write-Host "API ready." -ForegroundColor Green } else { Write-Host "API not ready yet - check the 'Probity API' window." -ForegroundColor Yellow }

Write-Host "`nProbity is starting:" -ForegroundColor Green
Write-Host "  App:      http://localhost:5180"
Write-Host "  API docs: http://127.0.0.1:8010/docs"
Write-Host "  Sign in with any demo user (e.g. Vikram Mehta, approver)."
Start-Process "http://localhost:5180"
