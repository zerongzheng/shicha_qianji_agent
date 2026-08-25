# Stop only the trigger process recorded by this project. Runtime configuration is preserved.
$ErrorActionPreference = "Continue"
$projectRoot = Split-Path -Parent $PSScriptRoot
$pidPath = Join-Path $projectRoot "outputs\wanwu_trigger.pid"

if (-not (Test-Path -LiteralPath $pidPath -PathType Leaf)) {
    Write-Host "Wanwu workflow trigger is not running." -ForegroundColor Yellow
    exit 0
}

$savedPid = [int](Get-Content -LiteralPath $pidPath -Raw)
$savedProcess = Get-Process -Id $savedPid -ErrorAction SilentlyContinue
if ($savedProcess -and $savedProcess.ProcessName -in @("powershell", "pwsh")) {
    Stop-Process -Id $savedPid -Force
    Write-Host "Wanwu workflow trigger stopped (PID $savedPid)." -ForegroundColor Green
}
else {
    Write-Host "Recorded Wanwu trigger process is no longer running." -ForegroundColor Yellow
}
Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue

