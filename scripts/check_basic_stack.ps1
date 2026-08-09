# Check Wanwu basic services, the Shichi Qianji API, and the captcha endpoint.
# Read-only: this script does not restart, stop, or modify services.

$ErrorActionPreference = "Continue"
$projectRoot = Split-Path -Parent $PSScriptRoot
$wanwuRoot = Join-Path (Split-Path -Parent $projectRoot) "wanwu"

Write-Host "=== Docker basic services ===" -ForegroundColor Cyan
Push-Location $wanwuRoot
try {
    $names = @(
        "mysql-wanwu", "redis-wanwu", "minio-wanwu", "kafka-wanwu", "elastic-wanwu",
        "bff-service", "iam-service", "model-service", "mcp-service", "knowledge-service",
        "rag-service", "assistant-service", "agent-service", "operate-service", "app-service",
        "channel-service", "callback-wanwu", "workflow-wanwu", "rag-wanwu", "wga-sandbox-wanwu",
        "nginx-wanwu"
    )
    foreach ($name in $names) {
        # Some Wanwu services do not define a Docker healthcheck. Check the
        # container state first, then show the health state when available.
        $state = docker inspect -f "{{.State.Status}}" $name 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Host ("{0,-24} missing" -f $name) -ForegroundColor Red
        }
        elseif ($state -eq "running") {
            $health = docker inspect -f "{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}" $name 2>$null
            Write-Host ("{0,-24} running ({1})" -f $name, $health) -ForegroundColor Green
        }
        else {
            Write-Host ("{0,-24} {1}" -f $name, $state) -ForegroundColor Yellow
        }
    }
}
finally {
    Pop-Location
}

function Test-Http($url, $label) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 10
        Write-Host ("{0,-24} HTTP {1}" -f $label, $response.StatusCode) -ForegroundColor Green
        return $true
    }
    catch {
        Write-Host ("{0,-24} failed: {1}" -f $label, $_.Exception.Message) -ForegroundColor Red
        return $false
    }
}

Write-Host "`n=== Shichi Qianji API ===" -ForegroundColor Cyan
Test-Http "http://127.0.0.1:8000/health" "API health" | Out-Null
Test-Http "http://127.0.0.1:8000/integrations/wanwu/openapi.json" "API OpenAPI" | Out-Null

Write-Host "`n=== Wanwu login chain ===" -ForegroundColor Cyan
$captchaUrl = "http://127.0.0.1:8081/user/api/v1/base/captcha"
try {
    $response = Invoke-WebRequest -UseBasicParsing -Uri $captchaUrl -TimeoutSec 15
    $payload = $response.Content | ConvertFrom-Json
    $length = if ($payload.data.b64) { $payload.data.b64.Length } else { 0 }
    if ($response.StatusCode -eq 200 -and $payload.data.key -and $length -gt 0) {
        Write-Host ("{0,-24} HTTP 200, captcha image ready (base64 length {1})" -f "captcha", $length) -ForegroundColor Green
    }
    else {
        Write-Host "Captcha endpoint returned success, but image data is empty." -ForegroundColor Yellow
    }
}
catch {
    Write-Host ("Captcha endpoint failed: {0}" -f $_.Exception.Message) -ForegroundColor Red
}

Write-Host "`n=== Resource note ===" -ForegroundColor Cyan
Write-Host "Keep vega-* and ontology containers stopped on a 16 GB machine." -ForegroundColor Yellow
