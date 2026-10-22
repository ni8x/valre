# --- Valorant Replay Lab launcher -------------------------------------
# Starts the local backend (FastAPI) which also serves the built frontend.
# Usage:  powershell -ExecutionPolicy Bypass -File .\run.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Write-Host "== Valorant Replay Lab ==" -ForegroundColor Cyan

# 1. vrfkit binary
if (-not (Test-Path "bin\vrfkit.exe")) {
    Write-Warning "bin\vrfkit.exe is missing. Download the vrfkit v0.3.0 Windows release and place it in .\bin\"
}

# 2. python deps
python -c "import fastapi, pyarrow, requests, uvicorn, multipart" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing python dependencies..." -ForegroundColor Yellow
    python -m pip install -r server\requirements.txt
}

# 3. frontend build
if (-not (Test-Path "web\dist\index.html")) {
    Write-Host "Building frontend..." -ForegroundColor Yellow
    Push-Location web
    try {
        if (-not (Test-Path node_modules)) { npm install }
        npm run build
    } finally { Pop-Location }
}

# 4. asset icons (agents/maps/abilities) - only if missing
if (-not (Test-Path "data\game\agents.json")) {
    Write-Host "Icons will download on first use (Replays page -> 'Download agent/map icons')." -ForegroundColor Yellow
}

Write-Host "Starting server at http://127.0.0.1:8000  (Ctrl+C to stop)" -ForegroundColor Green
Start-Process "http://127.0.0.1:8000" | Out-Null
python server\app.py
