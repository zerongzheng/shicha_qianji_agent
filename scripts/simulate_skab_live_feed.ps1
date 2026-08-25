param(
    [string]$SourceDirectory = "",
    [string]$TargetDirectory = "",
    [int]$IntervalSeconds = 60,
    [switch]$PrepareOnly,
    [switch]$RunOnce,
    [switch]$Replay,
    [switch]$TriggerAutonomousWorkflow,
    [string]$AutonomousWorkflowConfig = ""
)

$ErrorActionPreference = "Stop"

# 这是校赛演示用的数据接入模拟器，不修改 SKAB 原始文件，也不伪造企业数据。
# 它只把公开数据集中的下一份 CSV 复制到独立目录，模拟设备接口产生一个新批次。
$scriptPath = $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($scriptPath)) {
    $projectRoot = (Get-Location).Path
}
else {
    $scriptDirectory = Split-Path -Parent $scriptPath
    $projectRoot = Split-Path -Parent $scriptDirectory
}
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot "pyproject.toml") -PathType Leaf)) {
    throw "Run this script from the shicha_qianji_agent project root."
}
if ([string]::IsNullOrWhiteSpace($SourceDirectory)) {
    $SourceDirectory = Join-Path (Split-Path -Parent $projectRoot) "SKAB\data\valve1"
}
if ([string]::IsNullOrWhiteSpace($TargetDirectory)) {
    $TargetDirectory = Join-Path $projectRoot "outputs\demo_feed\skab_valve1"
}
if ([string]::IsNullOrWhiteSpace($AutonomousWorkflowConfig)) {
    $AutonomousWorkflowConfig = Join-Path `
        $projectRoot "outputs\wanwu_autonomous_workflow.local.json"
}
if ($TriggerAutonomousWorkflow -and -not $RunOnce) {
    throw "-TriggerAutonomousWorkflow 只能与 -RunOnce 一起使用，避免连续投递时重复发送通知。"
}

if (-not (Test-Path -LiteralPath $SourceDirectory -PathType Container)) {
    throw "SKAB source directory does not exist: $SourceDirectory"
}
New-Item -ItemType Directory -Path $TargetDirectory -Force | Out-Null

# 临时文件必须放在监测目录之外。否则后台采集器可能抢先发现并处理
# partial 文件，导致后续重命名时源文件已经不存在。
$stagingDirectory = [System.IO.Path]::Combine($projectRoot, "outputs", ".skab_feed_staging")
New-Item -ItemType Directory -Path $stagingDirectory -Force | Out-Null

$statePath = Join-Path $TargetDirectory ".feed_state.json"
$replayOffsetMilliseconds = 0
$replayActive = $false
if ($Replay) {
    $replayOffsetMilliseconds = [Math]::Max(1, [int]((Get-Date).Ticks % 3600000))
    $replayActive = $true
}
if ($Replay -and (Test-Path -LiteralPath $statePath -PathType Leaf)) {
    Remove-Item -LiteralPath $statePath -Force
    Write-Host "已重置模拟器进度；历史 CSV 和数据库记录未删除。" -ForegroundColor Yellow
}
$samples = @(Get-ChildItem -LiteralPath $SourceDirectory -File -Filter "*.csv" | Sort-Object {
    try { [int]$_.BaseName } catch { [int]::MaxValue }
}, Name)
if ($samples.Count -eq 0) {
    throw "No CSV samples found in: $SourceDirectory"
}

$nextIndex = 0
if (Test-Path -LiteralPath $statePath) {
    $state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
    $nextIndex = [int]$state.next_index
    if (-not $Replay -and
        $state.PSObject.Properties.Name -contains "replay_offset_milliseconds") {
        $savedReplayOffset = [int]$state.replay_offset_milliseconds
        if ($savedReplayOffset -gt 0) {
            $replayOffsetMilliseconds = $savedReplayOffset
            $replayActive = $true
        }
    }
}

if ($PrepareOnly) {
    Write-Host "Demo feed directory is ready: $TargetDirectory"
    exit 0
}

do {
    if ($nextIndex -ge $samples.Count) {
        Write-Host "All SKAB samples have been emitted. No new demo batch remains."
        break
    }

    $sample = $samples[$nextIndex]
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss_fff"
    $targetName = "batch_{0:D3}_{1}_{2}" -f $nextIndex, $timestamp, $sample.Name
    $temporaryPath = Join-Path $stagingDirectory ("." + $targetName + ".partial")
    $targetPath = Join-Path $TargetDirectory $targetName

    # 先在监测目录外完整写入，再一次性移入，监测器只会看到完整 CSV。
    if ($replayActive) {
        $firstLine = Get-Content -LiteralPath $sample.FullName -Encoding UTF8 -TotalCount 1
        $delimiter = if ($firstLine -match ";") { ";" } else { "," }
        $rows = @(Import-Csv -LiteralPath $sample.FullName -Delimiter $delimiter)
        if ($rows.Count -eq 0) {
            throw "无法重放空 CSV: $($sample.FullName)"
        }
        $timeProperty = $rows[0].PSObject.Properties.Name |
            Where-Object { $_ -match "^(datetime|timestamp|time)$" } |
            Select-Object -First 1
        if ([string]::IsNullOrWhiteSpace([string]$timeProperty)) {
            throw "重放 CSV 缺少时间列: $($sample.FullName)"
        }
        foreach ($row in $rows) {
            $parsedTime = [DateTime]::Parse([string]$row.$timeProperty)
            $row.$timeProperty = $parsedTime.AddMilliseconds(
                $replayOffsetMilliseconds
            ).ToString("o")
        }
        $rows | Export-Csv -LiteralPath $temporaryPath `
            -Delimiter $delimiter -NoTypeInformation -Encoding UTF8
    }
    else {
        [System.IO.File]::Copy($sample.FullName, $temporaryPath, $true)
    }
    [System.IO.File]::Move($temporaryPath, $targetPath)
    $nextIndex++
    @{
        next_index = $nextIndex
        last_emitted = $sample.Name
        emitted_at = (Get-Date).ToString("o")
        replay_offset_milliseconds = $replayOffsetMilliseconds
    } |
        ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8

    $replayLabel = if ($replayActive) { "（重放，时间轴已平移）" } else { "" }
    Write-Host ("Emitted SKAB sample {0} {1}-> {2}" -f $sample.Name, $replayLabel, $targetPath) `
        -ForegroundColor Green
    if ($TriggerAutonomousWorkflow) {
        if (-not (Test-Path -LiteralPath $AutonomousWorkflowConfig -PathType Leaf)) {
            throw "无人值守工作流配置不存在: $AutonomousWorkflowConfig"
        }
        $triggerScript = Join-Path $projectRoot "wanwu\scripts\trigger_wanwu_workflow.ps1"
        if (-not (Test-Path -LiteralPath $triggerScript -PathType Leaf)) {
            throw "万悟工作流触发脚本不存在: $triggerScript"
        }

        Write-Host "已投递新批次，立即触发万悟无人值守巡检；该工作流可能发送企业微信通知。" `
            -ForegroundColor Cyan
        $workflowStartedAt = Get-Date
        $workflowOutput = & $triggerScript `
            -ConfigPath $AutonomousWorkflowConfig -RunOnce | Out-String
        try {
            $workflowResult = $workflowOutput | ConvertFrom-Json
        }
        catch {
            throw "万悟工作流返回内容不是有效 JSON: $workflowOutput"
        }
        if ([string]$workflowResult.status -ne "success") {
            throw "万悟无人值守巡检触发失败: $($workflowResult.error)"
        }
        $elapsedSeconds = [Math]::Round(
            ((Get-Date) - $workflowStartedAt).TotalSeconds,
            1
        )
        Write-Host "万悟无人值守巡检已完成，本次调用耗时 ${elapsedSeconds} 秒。" `
            -ForegroundColor Green
        $workflowResult | ConvertTo-Json -Depth 12
    }
    if (-not $RunOnce) {
        Start-Sleep -Seconds ([Math]::Max(10, $IntervalSeconds))
    }
} while (-not $RunOnce)
