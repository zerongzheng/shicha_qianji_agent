# Stop Wanwu basic services safely.
# Never run down -v; do not delete databases, volumes, images, or containers.

$ErrorActionPreference = "Continue"
$projectRoot = Split-Path -Parent $PSScriptRoot
$wanwuRoot = Join-Path (Split-Path -Parent $projectRoot) "wanwu"
$baseContainers = @(
    "mysql-wanwu", "mysql-wanwu-setup", "redis-wanwu", "minio-wanwu", "kafka-wanwu",
    "elastic-wanwu", "elastic-wanwu-setup", "bff-service", "iam-service", "model-service",
    "mcp-service", "knowledge-service", "rag-service", "assistant-service", "agent-service",
    "operate-service", "app-service", "channel-service", "callback-wanwu", "workflow-wanwu",
    "rag-wanwu", "wga-sandbox-wanwu", "nginx-wanwu"
)

Write-Host "Stopping Wanwu basic containers; all data is preserved..." -ForegroundColor Cyan
docker stop --time 15 $baseContainers

Write-Host "If the API is running in a foreground window, press Ctrl+C there to stop it." -ForegroundColor Green
