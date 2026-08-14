param(
    [switch]$SkipApi,
    [switch]$SkipTrigger
)

# Start the Wanwu basic stack, the Shichi Qianji API, and the workflow trigger.
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

Write-Host "[1/5] Checking PostgreSQL service..." -ForegroundColor Cyan
$postgresService = Get-Service -Name "postgresql*" -ErrorAction SilentlyContinue |
    Select-Object -First 1
if (-not $postgresService) {
    throw "PostgreSQL Windows service was not found."
}
if ($postgresService.Status -ne "Running") {
    try {
        Start-Service -Name $postgresService.Name
        $postgresService.WaitForStatus("Running", (New-TimeSpan -Seconds 30))
    }
    catch {
        throw "PostgreSQL is stopped and could not be started. Run this script as administrator once."
    }
}
Write-Host "PostgreSQL service ready: $($postgresService.Name)" -ForegroundColor Green

Write-Host "[2/5] Checking Docker engine..." -ForegroundColor Cyan
$dockerVersion = docker version --format "{{.Server.Version}}" 2>$null
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($dockerVersion)) {
    $dockerDesktopCandidates = @(
        "E:\Tools\Docker\DockerDesktop\Docker Desktop.exe",
        "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    )
    $dockerDesktop = $dockerDesktopCandidates |
        Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
        Select-Object -First 1
    if (-not $dockerDesktop) {
        throw "Docker engine is unavailable and Docker Desktop was not found."
    }
    Write-Host "Docker is not running; starting Docker Desktop..." -ForegroundColor Yellow
    Start-Process -FilePath $dockerDesktop -WindowStyle Hidden | Out-Null
    $dockerReady = $false
    for ($attempt = 1; $attempt -le 60; $attempt++) {
        Start-Sleep -Seconds 2
        $dockerVersion = docker version --format "{{.Server.Version}}" 2>$null
        if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($dockerVersion)) {
            $dockerReady = $true
            break
        }
    }
    if (-not $dockerReady) {
        throw "Docker Desktop did not become ready within 120 seconds."
    }
}
Write-Host "Docker engine ready: $dockerVersion" -ForegroundColor Green

Write-Host "[3/5] Starting Wanwu basic services (ontology excluded)..." -ForegroundColor Cyan
Push-Location $wanwuRoot
try {
    docker compose @composeArgs up -d $baseServices
}
finally {
    Pop-Location
}

if (-not $SkipApi) {
    Write-Host "[4/5] Starting Shichi Qianji API on port 8000..." -ForegroundColor Cyan
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

if (-not $SkipTrigger) {
    Write-Host "[5/5] Starting Wanwu workflow trigger..." -ForegroundColor Cyan
    & (Join-Path $PSScriptRoot "start_wanwu_trigger.ps1")
}

Write-Host "Competition stack start command completed. Run check_basic_stack.ps1 next." -ForegroundColor Green
