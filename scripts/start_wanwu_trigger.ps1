param(
    [string]$ConfigPath = ""
)

# Start exactly one hidden Wanwu workflow trigger and keep its runtime files under outputs.
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$outputDirectory = Join-Path $projectRoot "outputs"
$pidPath = Join-Path $outputDirectory "wanwu_trigger.pid"
$stdoutPath = Join-Path $outputDirectory "wanwu_trigger.log"
$stderrPath = Join-Path $outputDirectory "wanwu_trigger.error.log"
$triggerScript = Join-Path $projectRoot "wanwu\scripts\trigger_wanwu_workflow.ps1"
if ([string]::IsNullOrWhiteSpace($ConfigPath)) {
    $ConfigPath = Join-Path $outputDirectory "wanwu_autonomous_workflow.local.json"
}

New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
    throw "Wanwu trigger config does not exist: $ConfigPath"
}

if (Test-Path -LiteralPath $pidPath -PathType Leaf) {
    $savedPid = [int](Get-Content -LiteralPath $pidPath -Raw)
    $savedProcess = Get-Process -Id $savedPid -ErrorAction SilentlyContinue
    if ($savedProcess -and $savedProcess.ProcessName -in @("powershell", "pwsh")) {
        Write-Host "Wanwu workflow trigger is already running (PID $savedPid)." -ForegroundColor Green
        exit 0
    }
}

$quotedScript = '"' + (Resolve-Path -LiteralPath $triggerScript).Path + '"'
$quotedConfig = '"' + (Resolve-Path -LiteralPath $ConfigPath).Path + '"'
$arguments = @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $quotedScript,
    "-ConfigPath", $quotedConfig
)
$process = Start-Process -FilePath "powershell.exe" -ArgumentList $arguments `
    -WorkingDirectory $projectRoot -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
Set-Content -LiteralPath $pidPath -Value $process.Id -Encoding ASCII
Start-Sleep -Seconds 3

if (-not (Get-Process -Id $process.Id -ErrorAction SilentlyContinue)) {
    $errorText = Get-Content -LiteralPath $stderrPath -Raw -ErrorAction SilentlyContinue
    throw "Wanwu workflow trigger exited during startup. $errorText"
}

Write-Host "Wanwu workflow trigger started (PID $($process.Id))." -ForegroundColor Green
Write-Host "Trigger log: $stdoutPath" -ForegroundColor DarkGray

