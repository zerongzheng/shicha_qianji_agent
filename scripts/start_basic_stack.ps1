param(
    [switch]$SkipApi
)

# Start the Wanwu basic stack and the Shichi Qianji API.
# This script intentionally excludes ontology services for low-memory machines.
# It never deletes containers, images, volumes, or database data.

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$wanwuRoot = Join-Path (Split-Path -Parent $projectRoot) "wanwu"
$uvPath = if (Test-Path "E:\Tools\uv\uv.exe") { "E:\Tools\uv\uv.exe" } else { "uv" }
$composeArgs = @(
    "--env-file", ".env",
    "--env-file", ".env.ontology",
    "--env-file", ".env.image.amd64"
)

$baseServices = @(
    "mysql", "mysql-setup", "redis", "minio", "kafka", "es-setup", "es",
    "bff-service", "iam-service", "model-service", "mcp-service",
    "knowledge-service", "rag-service", "assistant-service", "agent-service",
    "operate-service", "app-service", "channel-service", "callback", "workflow",
    "rag", "wga-sandbox", "nginx"
)

Write-Host "[1/3] Checking Docker engine..." -ForegroundColor Cyan
docker version --format "Server={{.Server.Version}}" | Out-Host

Write-Host "[2/3] Starting Wanwu basic services (ontology excluded)..." -ForegroundColor Cyan
Push-Location $wanwuRoot
try {
    docker compose @composeArgs up -d $baseServices
}
finally {
    Pop-Location
}

if (-not $SkipApi) {
    Write-Host "[3/3] Starting Shichi Qianji API on port 8000..." -ForegroundColor Cyan
    Push-Location $projectRoot
    try {
        if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) {
            Write-Host "Port 8000 is already in use; API start skipped." -ForegroundColor Yellow
        }
        else {
            Start-Process -FilePath $uvPath -ArgumentList "run python api_main.py" -WorkingDirectory $projectRoot
            Write-Host "Shichi Qianji API started in a new process." -ForegroundColor Green
        }
    }
    finally {
        Pop-Location
    }
}

Write-Host "Basic stack start command completed. Run check_basic_stack.ps1 next." -ForegroundColor Green
