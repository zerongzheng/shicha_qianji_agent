# Stop the Shichi Qianji API and all four project-owned workflow triggers.
# The optional Vue3 console is stopped through its project-owned PID file.
# Wanwu data, PostgreSQL data, containers, images, and volumes are preserved.

$ErrorActionPreference = "Continue"
$projectRoot = Split-Path -Parent $PSScriptRoot
$wanwuRoot = Join-Path (Split-Path -Parent $projectRoot) "wanwu"
$outputDirectory = Join-Path $projectRoot "outputs"
$apiPidPath = Join-Path $outputDirectory "shichi_qianji_api.pid"
$frontendPidPath = Join-Path $outputDirectory "shichi_qianji_frontend.pid"
$triggerScriptName = "trigger_wanwu_workflow.ps1"
$baseContainers = @(
    "mysql-wanwu", "mysql-wanwu-setup", "redis-wanwu", "minio-wanwu", "kafka-wanwu",
    "elastic-wanwu", "elastic-wanwu-setup", "bff-service", "iam-service", "model-service",
    "mcp-service", "knowledge-service", "rag-service", "assistant-service", "agent-service",
    "operate-service", "app-service", "channel-service", "callback-wanwu", "workflow-wanwu",
    "rag-wanwu", "wga-sandbox-wanwu", "nginx-wanwu"
)

$workflowDefinitions = @(
    [PSCustomObject]@{
        Label = "无人值守巡检"
        ConfigName = "wanwu_autonomous_workflow.local.json"
        PidPath = Join-Path $outputDirectory "wanwu_autonomous_trigger.pid"
    },
    [PSCustomObject]@{
        Label = "SLA 督办"
        ConfigName = "wanwu_sla_workflow.local.json"
        PidPath = Join-Path $outputDirectory "wanwu_sla_trigger.pid"
    },
    [PSCustomObject]@{
        Label = "维修后复检"
        ConfigName = "wanwu_reinspection_workflow.local.json"
        PidPath = Join-Path $outputDirectory "wanwu_reinspection_trigger.pid"
    },
    [PSCustomObject]@{
        Label = "班次简报"
        ConfigName = "wanwu_shift_brief_workflow.local.json"
        PidPath = Join-Path $outputDirectory "wanwu_shift_brief_trigger.pid"
    }
)

function Get-ProcessCommandLine([int]$ProcessId) {
    try {
        return [string](
            Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop |
                Select-Object -ExpandProperty CommandLine
        )
    }
    catch {
        return ""
    }
}

function Test-TrackedProcessIdentity(
    [System.Diagnostics.Process]$Process,
    [string[]]$CommandTokens,
    [string[]]$FallbackProcessNames
) {
    $commandLine = Get-ProcessCommandLine $Process.Id
    if (-not [string]::IsNullOrWhiteSpace($commandLine)) {
        foreach ($token in $CommandTokens) {
            if ($commandLine.IndexOf($token, [System.StringComparison]::OrdinalIgnoreCase) -lt 0) {
                return $false
            }
        }
        return $true
    }

    # Restricted Windows sessions may deny Win32_Process.CommandLine. The PID
    # file is project-owned, so use the executable type as a narrow fallback.
    return $FallbackProcessNames -contains $Process.ProcessName.ToLowerInvariant()
}

function Get-DescendantProcessIds([int]$ParentId) {
    $children = @(
        Get-CimInstance Win32_Process -Filter "ParentProcessId = $ParentId" `
            -ErrorAction SilentlyContinue
    )
    foreach ($child in $children) {
        @(Get-DescendantProcessIds ([int]$child.ProcessId))
        [int]$child.ProcessId
    }
}

function Stop-RecordedProcess(
    [string]$PidPath,
    [string[]]$CommandTokens,
    [string]$Label,
    [string[]]$FallbackProcessNames
) {
    if (-not (Test-Path -LiteralPath $PidPath -PathType Leaf)) {
        Write-Host "$Label 未发现 PID 文件。" -ForegroundColor DarkGray
        return
    }

    $savedPid = 0
    $rawPid = (Get-Content -LiteralPath $PidPath -Raw -ErrorAction SilentlyContinue).Trim()
    if (-not [int]::TryParse($rawPid, [ref]$savedPid) -or $savedPid -le 0) {
        Write-Host "$Label PID 文件无效，已清理。" -ForegroundColor Yellow
        Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
        return
    }

    $process = Get-Process -Id $savedPid -ErrorAction SilentlyContinue
    $matches = $null -ne $process -and
        (Test-TrackedProcessIdentity $process $CommandTokens $FallbackProcessNames)

    if ($matches) {
        $childIds = @(Get-DescendantProcessIds $savedPid)
        foreach ($childId in $childIds) {
            Stop-Process -Id $childId -Force -ErrorAction SilentlyContinue
        }
        Stop-Process -Id $savedPid -Force -ErrorAction SilentlyContinue
        Write-Host "$Label 已停止（PID $savedPid）。" -ForegroundColor Green
    }
    elseif ($process) {
        Write-Host "$Label PID $savedPid 不匹配项目进程，未停止。" -ForegroundColor Yellow
    }
    else {
        Write-Host "$Label 进程已退出。" -ForegroundColor Yellow
    }
    Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
}

New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null

Stop-RecordedProcess $apiPidPath @("api_main.py") "时察千机后端" @("uv", "python")
Stop-RecordedProcess $frontendPidPath @("vite", "frontend") "Vue3 运维工作台" @("node")
foreach ($definition in $workflowDefinitions) {
    Stop-RecordedProcess $definition.PidPath @(
        $triggerScriptName,
        $definition.ConfigName
    ) "$($definition.Label)触发器" @("powershell", "pwsh")
}

# Clean up the PID file used by the legacy single-trigger helper, but only stop
# it when its command line still points at the autonomous workflow.
$legacyPidPath = Join-Path $outputDirectory "wanwu_trigger.pid"
Stop-RecordedProcess $legacyPidPath @(
    $triggerScriptName,
    "wanwu_autonomous_workflow.local.json"
) "旧版无人值守巡检触发器" @("powershell", "pwsh")

Write-Host "Stopping Wanwu basic containers; all data is preserved..." -ForegroundColor Cyan
Push-Location $wanwuRoot
try {
    docker stop --time 15 $baseContainers
}
finally {
    Pop-Location
}

Write-Host "PostgreSQL service was left running so database data remains immediately available." `
    -ForegroundColor DarkGray
