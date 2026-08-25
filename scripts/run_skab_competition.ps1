param(
    [switch]$Rerun,
    [switch]$EvidencePack
)

# 生成 SKAB 校赛实验、竞赛成效和答辩证据材料。
# 默认复用 outputs/competition 中已经冻结的实验结果；只有指定 -Rerun 时，
# 才会重新进行阈值调优和独立测试，避免误操作触发长时间计算。

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$skabDataRoot = Join-Path (Split-Path -Parent $projectRoot) "SKAB\data"
$uvPath = if (Test-Path "E:\Tools\uv\uv.exe") { "E:\Tools\uv\uv.exe" } else { "uv" }

if (-not (Test-Path -LiteralPath $skabDataRoot -PathType Container)) {
    throw "找不到 SKAB 数据目录：$skabDataRoot"
}

Push-Location $projectRoot
try {
    $arguments = @("run", "python", "main.py")
    if ($EvidencePack) {
        $arguments += @("--evidence-pack", "--case-count", "3")
    }
    else {
        $arguments += @("--competition-report")
    }
    $arguments += @("--data-root", $skabDataRoot)
    if ($Rerun) {
        $arguments += "--rerun-competition-report"
    }

    Write-Host "正在生成 SKAB 校赛实验材料..." -ForegroundColor Cyan
    & $uvPath @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "实验命令执行失败，退出码：$LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

if ($EvidencePack) {
    Write-Host "答辩证据包已生成到：$projectRoot\outputs\evidence_pack" -ForegroundColor Green
    Write-Host "重点查看：EVIDENCE_PACK_INDEX.md、趋势预测报告和误报审计报告" -ForegroundColor Green
}
else {
    Write-Host "材料已生成到：$projectRoot\outputs\competition" -ForegroundColor Green
    Write-Host "重点查看：skab_competition_summary.md、forecast_effectiveness_*.md 和 SKAB_EXPERIMENT_PROTOCOL.md" -ForegroundColor Green
}
