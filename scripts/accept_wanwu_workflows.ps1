param(
    [string]$AutonomousConfigPath = ".\outputs\wanwu_autonomous_workflow.local.json",
    [string]$SlaConfigPath = ".\outputs\wanwu_sla_workflow.local.json",
    [string]$ReinspectionConfigPath = ".\outputs\wanwu_reinspection_workflow.local.json",
    [string]$ShiftBriefConfigPath = ".\outputs\wanwu_shift_brief_workflow.local.json",
    [string]$ReportPath = ".\outputs\wanwu_acceptance_report.json",
    [switch]$RunWorkflows,
    [switch]$InjectSample
)

$ErrorActionPreference = "Stop"
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$projectRoot = Split-Path -Parent $PSScriptRoot
$expectedTools = @(
    "run_unattended_industrial_cycle",
    "get_industrial_analysis_status",
    "get_industrial_decision_brief",
    "dispatch_industrial_alerts",
    "run_industrial_sla_cycle",
    "run_industrial_reinspection_cycle",
    "generate_industrial_shift_brief"
)

function Resolve-ProjectPath([string]$PathValue) {
    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return $PathValue
    }
    return [System.IO.Path]::GetFullPath((Join-Path $projectRoot $PathValue))
}

function Test-WorkflowConfig([string]$PathValue, [string]$ExpectedPlaceholder) {
    $resolved = Resolve-ProjectPath $PathValue
    if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
        return [PSCustomObject]@{
            path = $resolved
            ready = $false
            message = "配置文件不存在"
        }
    }
    try {
        $config = Get-Content -LiteralPath $resolved -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    catch {
        return [PSCustomObject]@{
            path = $resolved
            ready = $false
            message = "配置文件不是有效 JSON"
        }
    }
    $uuid = [string]$config.workflow_uuid
    $keyName = if ($config.PSObject.Properties.Name -contains "api_key_env") {
        [string]$config.api_key_env
    }
    else {
        "WANWU_WORKFLOW_API_KEY"
    }
    $hasUuid = $uuid -match "^\d+$" -and $uuid -ne "0" -and $uuid -ne $ExpectedPlaceholder
    $hasKey = -not [string]::IsNullOrWhiteSpace(
        [Environment]::GetEnvironmentVariable($keyName)
    )
    if (-not $hasKey) {
        $envPath = Join-Path $projectRoot ".env"
        if (Test-Path -LiteralPath $envPath -PathType Leaf) {
            $escapedName = [Regex]::Escape($keyName)
            $hasKey = [bool](
                Get-Content -LiteralPath $envPath -Encoding UTF8 |
                    Where-Object { $_ -match ("^\s*" + $escapedName + "\s*=\s*.+$") } |
                    Select-Object -First 1
            )
        }
    }
    return [PSCustomObject]@{
        path = $resolved
        ready = $hasUuid -and $hasKey
        workflow_uuid_configured = $hasUuid
        api_key_env = $keyName
        api_key_configured = $hasKey
        message = if ($hasUuid -and $hasKey) { "配置完整" } else { "缺少工作流 UUID 或 API Key" }
    }
}

function Invoke-WorkflowOnce([string]$ConfigPathValue) {
    $trigger = Join-Path $projectRoot "wanwu\scripts\trigger_wanwu_workflow.ps1"
    $raw = & $trigger -ConfigPath (Resolve-ProjectPath $ConfigPathValue) -RunOnce
    if ($LASTEXITCODE -ne 0) {
        throw "工作流触发脚本执行失败"
    }
    return ($raw | Out-String | ConvertFrom-Json)
}

$checks = [ordered]@{}
try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -TimeoutSec 10
    $checks.backend = [PSCustomObject]@{
        ready = $health.status -eq "ok"
        service = $health.service
        database = $health.database
    }
}
catch {
    $checks.backend = [PSCustomObject]@{ ready = $false; message = $_.Exception.Message }
}

try {
    $schema = Invoke-RestMethod `
        -Uri "http://127.0.0.1:8000/integrations/wanwu/openapi.json" -TimeoutSec 15
    $operationIds = @(
        $schema.paths.PSObject.Properties.Value |
            ForEach-Object { $_.PSObject.Properties.Value.operationId } |
            Where-Object { $_ }
    )
    $missingTools = @($expectedTools | Where-Object { $_ -notin $operationIds })
    $checks.openapi = [PSCustomObject]@{
        ready = $missingTools.Count -eq 0 -and $operationIds.Count -eq 19
        tool_count = $operationIds.Count
        missing_required_tools = $missingTools
    }
}
catch {
    $checks.openapi = [PSCustomObject]@{ ready = $false; message = $_.Exception.Message }
}

try {
    $platform = Invoke-WebRequest -UseBasicParsing `
        -Uri "http://127.0.0.1:8081/user/api/v1/base/captcha" -TimeoutSec 15
    $platformPayload = $platform.Content | ConvertFrom-Json
    $checks.wanwu_platform = [PSCustomObject]@{
        ready = $platform.StatusCode -eq 200 -and
            $null -ne $platformPayload.data -and
            -not [string]::IsNullOrWhiteSpace([string]$platformPayload.data.key) -and
            -not [string]::IsNullOrWhiteSpace([string]$platformPayload.data.b64)
        http_status = $platform.StatusCode
        endpoint = "/user/api/v1/base/captcha"
    }
}
catch {
    $checks.wanwu_platform = [PSCustomObject]@{ ready = $false; message = $_.Exception.Message }
}

$autonomousConfig = Test-WorkflowConfig $AutonomousConfigPath "WANWU_WORKFLOW_UUID"
$slaConfig = Test-WorkflowConfig $SlaConfigPath "WANWU_SLA_WORKFLOW_UUID"
$reinspectionConfig = Test-WorkflowConfig $ReinspectionConfigPath "WANWU_REINSPECTION_WORKFLOW_UUID"
$shiftConfig = Test-WorkflowConfig $ShiftBriefConfigPath "WANWU_SHIFT_BRIEF_WORKFLOW_UUID"
$checks.autonomous_workflow_config = $autonomousConfig
$checks.sla_workflow_config = $slaConfig
$checks.reinspection_workflow_config = $reinspectionConfig
$checks.shift_brief_workflow_config = $shiftConfig

if ($InjectSample) {
    $simulator = Join-Path $projectRoot "scripts\simulate_skab_live_feed.ps1"
    $sampleOutput = & $simulator -RunOnce | Out-String
    $checks.sample_injection = [PSCustomObject]@{
        executed = $true
        message = $sampleOutput.Trim()
    }
}
else {
    $checks.sample_injection = [PSCustomObject]@{
        executed = $false
        message = "未投递样本；使用 -InjectSample 才会产生新批次"
    }
}

if ($RunWorkflows) {
    if (-not $autonomousConfig.ready -or -not $slaConfig.ready -or
        -not $reinspectionConfig.ready -or -not $shiftConfig.ready) {
        throw "四个运行工作流配置未就绪，不能执行验收调用"
    }
    $checks.autonomous_workflow_run = Invoke-WorkflowOnce $AutonomousConfigPath
    $checks.sla_workflow_run = Invoke-WorkflowOnce $SlaConfigPath
    $checks.reinspection_workflow_run = Invoke-WorkflowOnce $ReinspectionConfigPath
    $checks.shift_brief_workflow_run = Invoke-WorkflowOnce $ShiftBriefConfigPath
}
else {
    $checks.workflow_execution = [PSCustomObject]@{
        executed = $false
        message = "未调用工作流；使用 -RunWorkflows 才会执行"
    }
}

$requiredReady = @(
    $checks.backend.ready,
    $checks.openapi.ready,
    $checks.wanwu_platform.ready,
    $checks.autonomous_workflow_config.ready,
    $checks.sla_workflow_config.ready,
    $checks.reinspection_workflow_config.ready,
    $checks.shift_brief_workflow_config.ready
)
$report = [PSCustomObject]@{
    status = if ($requiredReady -notcontains $false) { "ready" } else { "needs_attention" }
    generated_at = (Get-Date).ToString("o")
    run_workflows = [bool]$RunWorkflows
    inject_sample = [bool]$InjectSample
    checks = $checks
    note = "SKAB 投递只验证自动化机制，不代表企业现场成效。"
}
$resolvedReportPath = Resolve-ProjectPath $ReportPath
New-Item -ItemType Directory -Path (Split-Path -Parent $resolvedReportPath) -Force | Out-Null
$report | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $resolvedReportPath -Encoding UTF8
$report | ConvertTo-Json -Depth 20

if ($report.status -ne "ready") {
    exit 1
}
