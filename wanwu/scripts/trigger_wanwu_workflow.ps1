param(
    [Parameter(Mandatory = $true)]
    [string]$ConfigPath,
    [switch]$RunOnce
)

$ErrorActionPreference = "Stop"
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
$apiKeyEnvironmentName = if (
    $config.PSObject.Properties.Name -contains "api_key_env" -and
    -not [string]::IsNullOrWhiteSpace([string]$config.api_key_env)
) {
    [string]$config.api_key_env
}
else {
    "WANWU_WORKFLOW_API_KEY"
}
$apiKey = [Environment]::GetEnvironmentVariable($apiKeyEnvironmentName)
if ([string]::IsNullOrWhiteSpace($apiKey)) {
    $scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
    $projectRoot = Split-Path -Parent (Split-Path -Parent $scriptDirectory)
    $envPath = Join-Path $projectRoot ".env"
    if (Test-Path -LiteralPath $envPath -PathType Leaf) {
        foreach ($line in Get-Content -LiteralPath $envPath -Encoding UTF8) {
            $escapedName = [Regex]::Escape($apiKeyEnvironmentName)
            if ($line -match ("^\s*" + $escapedName + "\s*=\s*(.*)\s*$")) {
                $apiKey = $Matches[1].Trim().Trim('"').Trim("'")
                break
            }
        }
    }
}
if ([string]::IsNullOrWhiteSpace($apiKey)) {
    throw "$apiKeyEnvironmentName is not configured in the process environment or project .env."
}
if ([string]::IsNullOrWhiteSpace([string]$config.workflow_uuid) -or
    [string]$config.workflow_uuid -eq "WANWU_WORKFLOW_UUID") {
    throw "workflow_uuid is not configured."
}

$url = ([string]$config.wanwu_base_url).TrimEnd("/") + "/service/api/openapi/v1/workflow/run"
$interval = [Math]::Max(10, [int]$config.interval_seconds)
$headers = @{ Authorization = "Bearer $apiKey"; Accept = "application/json" }
$body = @{
    uuid = [string]$config.workflow_uuid
    parameters = $config.parameters
} | ConvertTo-Json -Depth 10

do {
    $startedAt = Get-Date
    try {
        $result = Invoke-RestMethod -Uri $url -Method Post -Headers $headers `
            -ContentType "application/json; charset=utf-8" -Body $body -TimeoutSec 300
        [PSCustomObject]@{
            timestamp = $startedAt.ToString("yyyy-MM-dd HH:mm:ss")
            status = "success"
            result = $result
        } | ConvertTo-Json -Depth 12
    }
    catch {
        [PSCustomObject]@{
            timestamp = $startedAt.ToString("yyyy-MM-dd HH:mm:ss")
            status = "failed"
            error = $_.Exception.Message
        } | ConvertTo-Json -Depth 4
    }
    if (-not $RunOnce) {
        Start-Sleep -Seconds $interval
    }
} while (-not $RunOnce)
