# Probity - start local development (Windows PowerShell).
#
#   powershell -ExecutionPolicy Bypass -File scripts\dev.ps1
#
# 1. Starts Postgres in Docker.
# 2. First run: creates apps\api\.env and generates the two app secrets (never printed).
# 3. Prints the live/missing checklist and creates the empty schema. No data is inserted.
# 4. If every required setting is present, opens two windows: API and web. Close them to stop.
#    `docker compose -f infra\docker-compose.dev.yml down` stops Postgres.
#
# Investigations run inside the API process (TASK_BACKEND=inline). No worker window is needed.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$api = Join-Path $root "apps\api"
$web = Join-Path $root "apps\web"
$py = Join-Path $root ".venv\Scripts\python.exe"
$envFile = Join-Path $api ".env"

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

Step "Checking prerequisites"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw "Docker is not installed. Install Docker Desktop and start it." }
docker info *> $null
if ($LASTEXITCODE -ne 0) { throw "Docker Desktop is not running. Start it and run this script again." }
if (-not (Test-Path $py)) {
    Step "Creating Python environment (first run only)"
    python -m venv (Join-Path $root ".venv")
    & $py -m pip install --upgrade pip
    & $py -m pip install -e "$api[dev,worker]"
}
if (-not (Test-Path (Join-Path $web "node_modules"))) {
    Step "Installing web dependencies (first run only)"
    Push-Location $web; npm install; Pop-Location
}

Step "Starting Postgres (Docker)"
docker compose -f (Join-Path $root "infra\docker-compose.dev.yml") up -d --wait
if ($LASTEXITCODE -ne 0) { throw "Postgres failed to start." }

Push-Location $api
if (-not (Test-Path $envFile)) {
    Step "Creating apps\api\.env (first run only)"
    & $py -m probity.bootstrap --generate-secrets | Out-Null
    $text = Get-Content $envFile -Raw
    $text = $text -replace '(?m)^DATABASE_URL=.*$', 'DATABASE_URL=postgresql+psycopg://probity_app:probity_app@localhost:5434/probity'
    $text = $text -replace '(?m)^DATABASE_MIGRATE_URL=.*$', 'DATABASE_MIGRATE_URL=postgresql+psycopg://probity:probity@localhost:5434/probity'
    Set-Content -Path $envFile -Value $text -Encoding utf8 -NoNewline
}

Step "Checking configuration and creating the database schema"
& $py -m probity.bootstrap
$status = $LASTEXITCODE
Pop-Location
if ($status -ne 0) {
    Write-Host "`nAdd the missing values to apps\api\.env, then run this script again." -ForegroundColor Yellow
    Write-Host "  ANTHROPIC_API_KEY     console.anthropic.com > API keys"
    Write-Host "  CLERK_ISSUER          Clerk dashboard > API keys > Frontend API URL (https://...clerk.accounts.dev)"
    Write-Host "  CLERK_SECRET_KEY      Clerk dashboard > API keys > Secret key"
    Write-Host "  EMAIL_ALLOWLIST       your own address(es), comma-separated (only these receive email)"
    exit 1
}

$webEnv = Join-Path $web ".env.local"
if (-not ((Test-Path $webEnv) -and (Select-String -Path $webEnv -Pattern '^VITE_CLERK_PUBLISHABLE_KEY=\S+' -Quiet))) {
    Write-Host "`nThe web app needs VITE_CLERK_PUBLISHABLE_KEY=pk_... in apps\web\.env.local (Clerk dashboard > API keys)." -ForegroundColor Yellow
}

Step "Starting API and web in their own windows"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "`$Host.UI.RawUI.WindowTitle='Probity API'; Set-Location '$api'; & '$py' -m uvicorn probity.api.main:app --host 127.0.0.1 --port 8010 --reload"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "`$Host.UI.RawUI.WindowTitle='Probity web'; Set-Location '$web'; npm run dev"

Step "Waiting for the API"
$ok = $false
for ($i = 0; $i -lt 40; $i++) {
    try { $r = Invoke-RestMethod -Uri "http://127.0.0.1:8010/api/v1/ready" -TimeoutSec 2; if ($r.ok) { $ok = $true; break } } catch { }
    Start-Sleep -Seconds 1
}
if ($ok) { Write-Host "API ready." -ForegroundColor Green } else { Write-Host "API not ready yet - check the 'Probity API' window." -ForegroundColor Yellow }

Write-Host "`nProbity is starting:" -ForegroundColor Green
Write-Host "  App:      http://localhost:5180   (sign up with Clerk; your first sign-in creates your workspace)"
Write-Host "  API docs: http://127.0.0.1:8010/docs"
Start-Process "http://localhost:5180"
