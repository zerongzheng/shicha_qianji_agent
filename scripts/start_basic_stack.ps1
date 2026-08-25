param(
    [switch]$SkipApi,
    [switch]$SkipTrigger,
    [switch]$SkipFrontend,
    [switch]$IncludeFrontend
)

# Start PostgreSQL, the low-memory Wanwu Docker stack, the Shicha Qianji API,
# the four published Wanwu workflow triggers, and the Vue3 console.
# This script never deletes containers, images, volumes, or database data.

$ErrorActionPreference = "Stop"
if ($SkipFrontend -and $IncludeFrontend) {
    throw "-SkipFrontend 与兼容参数 -IncludeFrontend 不能同时使用。"
}
# -IncludeFrontend is retained for compatibility; the frontend now starts by default.
$startFrontend = -not $SkipFrontend
$projectRoot = Split-Path -Parent $PSScriptRoot
$wanwuRoot = Join-Path (Split-Path -Parent $projectRoot) "wanwu"
$outputDirectory = Join-Path $projectRoot "outputs"
$frontendRoot = Join-Path $projectRoot "frontend"
$uvPath = if (Test-Path "E:\Tools\uv\uv.exe") { "E:\Tools\uv\uv.exe" } else { "uv" }
$triggerScript = Join-Path $projectRoot "wanwu\scripts\trigger_wanwu_workflow.ps1"
$apiPidPath = Join-Path $outputDirectory "shicha_qianji_api.pid"
$apiStdoutPath = Join-Path $outputDirectory "shicha_qianji_api.log"
$apiStderrPath = Join-Path $outputDirectory "shicha_qianji_api.error.log"
$frontendPidPath = Join-Path $outputDirectory "shicha_qianji_frontend.pid"
$frontendStdoutPath = Join-Path $outputDirectory "shicha_qianji_frontend.log"
$frontendStderrPath = Join-Path $outputDirectory "shicha_qianji_frontend.error.log"

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

$workflowDefinitions = @(
    [PSCustomObject]@{
        Name = "autonomous"
        Label = "无人值守巡检"
        ConfigPath = Join-Path $outputDirectory "wanwu_autonomous_workflow.local.json"
        PidPath = Join-Path $outputDirectory "wanwu_autonomous_trigger.pid"
        StdoutPath = Join-Path $outputDirectory "wanwu_autonomous_trigger.log"
        StderrPath = Join-Path $outputDirectory "wanwu_autonomous_trigger.error.log"
    },
    [PSCustomObject]@{
        Name = "sla"
        Label = "SLA 督办"
        ConfigPath = Join-Path $outputDirectory "wanwu_sla_workflow.local.json"
        PidPath = Join-Path $outputDirectory "wanwu_sla_trigger.pid"
        StdoutPath = Join-Path $outputDirectory "wanwu_sla_trigger.log"
        StderrPath = Join-Path $outputDirectory "wanwu_sla_trigger.error.log"
    },
    [PSCustomObject]@{
        Name = "reinspection"
        Label = "维修后复检"
        ConfigPath = Join-Path $outputDirectory "wanwu_reinspection_workflow.local.json"
        PidPath = Join-Path $outputDirectory "wanwu_reinspection_trigger.pid"
        StdoutPath = Join-Path $outputDirectory "wanwu_reinspection_trigger.log"
        StderrPath = Join-Path $outputDirectory "wanwu_reinspection_trigger.error.log"
    },
    [PSCustomObject]@{
        Name = "shift_brief"
        Label = "班次简报"
        ConfigPath = Join-Path $outputDirectory "wanwu_shift_brief_workflow.local.json"
        PidPath = Join-Path $outputDirectory "wanwu_shift_brief_trigger.pid"
        StdoutPath = Join-Path $outputDirectory "wanwu_shift_brief_trigger.log"
        StderrPath = Join-Path $outputDirectory "wanwu_shift_brief_trigger.error.log"
    }
)

function Get-ContainerState([string]$containerName) {
    $state = docker inspect -f "{{.State.Status}}" $containerName 2>$null
    if ($LASTEXITCODE -ne 0) { return "missing" }
    return $state.Trim()
}

function Get-ContainerHealth([string]$containerName) {
    $health = docker inspect -f "{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}" $containerName 2>$null
    if ($LASTEXITCODE -ne 0) { return "missing" }
    return $health.Trim()
}

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

function Get-ApiRootProcessId([int]$ProcessId) {
    $current = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" `
        -ErrorAction SilentlyContinue
    if (-not $current) {
        return $ProcessId
    }
    $root = $current
    while ([int]$current.ParentProcessId -gt 0) {
        $parent = Get-CimInstance Win32_Process `
            -Filter "ProcessId = $($current.ParentProcessId)" `
            -ErrorAction SilentlyContinue
        if (-not $parent -or
            [string]$parent.CommandLine -notmatch "api_main\.py") {
            break
        }
        $root = $parent
        $current = $parent
    }
    return [int]$root.ProcessId
}

function Remove-StalePid([string]$PidPath) {
    Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
}

function Test-TrackedProcess(
    [string]$PidPath,
    [string[]]$CommandTokens,
    [string[]]$FallbackProcessNames
) {
    if (-not (Test-Path -LiteralPath $PidPath -PathType Leaf)) {
        return $null
    }

    $savedPid = 0
    $rawPid = (Get-Content -LiteralPath $PidPath -Raw -ErrorAction SilentlyContinue).Trim()
    if (-not [int]::TryParse($rawPid, [ref]$savedPid) -or $savedPid -le 0) {
        Remove-StalePid $PidPath
        return $null
    }

    $process = Get-Process -Id $savedPid -ErrorAction SilentlyContinue
    if (-not $process) {
        Remove-StalePid $PidPath
        return $null
    }

    if (-not (Test-TrackedProcessIdentity $process $CommandTokens $FallbackProcessNames)) {
        Remove-StalePid $PidPath
        return $null
    }
    return $process
}

function Get-EnvFileValue([string]$Name) {
    $processValue = [Environment]::GetEnvironmentVariable($Name)
    if (-not [string]::IsNullOrWhiteSpace($processValue)) {
        return $processValue.Trim()
    }

    $envPath = Join-Path $projectRoot ".env"
    if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) {
        return ""
    }
    $escapedName = [Regex]::Escape($Name)
    foreach ($line in Get-Content -LiteralPath $envPath -Encoding UTF8) {
        if ($line -match ("^\s*" + $escapedName + "\s*=\s*(.*?)\s*$")) {
            return $Matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return ""
}

function Read-WorkflowConfig($definition) {
    if (-not (Test-Path -LiteralPath $definition.ConfigPath -PathType Leaf)) {
        throw "$($definition.Label) 配置文件不存在: $($definition.ConfigPath)"
    }
    try {
        return Get-Content -LiteralPath $definition.ConfigPath -Raw -Encoding UTF8 |
            ConvertFrom-Json
    }
    catch {
        throw "$($definition.Label) 配置文件不是有效 JSON: $($definition.ConfigPath)"
    }
}

function Assert-WorkflowConfigs {
    foreach ($definition in $workflowDefinitions) {
        $config = Read-WorkflowConfig $definition
        $uuid = [string]$config.workflow_uuid
        if ($uuid -notmatch "^\d+$" -or $uuid -eq "0") {
            throw "$($definition.Label) workflow_uuid 未配置为有效的已发布 UUID: $($definition.ConfigPath)"
        }

        $apiKeyEnv = if ($config.PSObject.Properties.Name -contains "api_key_env" -and
            -not [string]::IsNullOrWhiteSpace([string]$config.api_key_env)) {
            [string]$config.api_key_env
        }
        else {
            "WANWU_WORKFLOW_API_KEY"
        }
        if ([string]::IsNullOrWhiteSpace((Get-EnvFileValue $apiKeyEnv))) {
            throw "$($definition.Label) 未配置 API Key: $apiKeyEnv（请检查进程环境变量或 .env）"
        }
        if ([string]::IsNullOrWhiteSpace([string]$config.wanwu_base_url)) {
            throw "$($definition.Label) 未配置 wanwu_base_url: $($definition.ConfigPath)"
        }
        Write-Host "$($definition.Label) 配置已校验: UUID=$uuid, API Key=$apiKeyEnv" -ForegroundColor Green
    }
}

function Wait-WanwuUpstreams {
    # Docker 重启后容器地址可能变化。必须等 BFF 和工作流服务稳定后再刷新 Nginx，
    # 否则 Nginx 可能缓存启动阶段的旧地址，页面会连续出现 502 Bad Gateway。
    $deadline = (Get-Date).AddMinutes(4)
    do {
        $bffState = Get-ContainerState "bff-service"
        $bffHealth = Get-ContainerHealth "bff-service"
        $workflowState = Get-ContainerState "workflow-wanwu"
        if ($bffState -eq "running" -and $bffHealth -eq "healthy" -and
            $workflowState -eq "running") {
            return
        }
        Write-Host "Waiting for Wanwu upstreams: BFF=$bffState/$bffHealth, workflow=$workflowState" `
            -ForegroundColor Yellow
        Start-Sleep -Seconds 5
    } while ((Get-Date) -lt $deadline)

    throw "Wanwu BFF or workflow service did not become ready within 4 minutes."
}

function Wait-WanwuGateway {
    $deadline = (Get-Date).AddMinutes(2)
    do {
        try {
            $response = Invoke-WebRequest -UseBasicParsing `
                -Uri "http://127.0.0.1:8081/user/api/v1/base/captcha" `
                -TimeoutSec 10
            if ($response.StatusCode -eq 200) { return }
        }
        catch {
            Write-Host "Waiting for Wanwu gateway: $($_.Exception.Message)" -ForegroundColor Yellow
        }
        Start-Sleep -Seconds 4
    } while ((Get-Date) -lt $deadline)

    throw "Wanwu gateway did not become ready within 2 minutes. Run check_basic_stack.ps1 for details."
}

function Wait-ShichaQianjiApi {
    $deadline = (Get-Date).AddMinutes(2)
    do {
        try {
            $response = Invoke-WebRequest -UseBasicParsing `
                -Uri "http://127.0.0.1:8000/health" -TimeoutSec 8
            if ($response.StatusCode -eq 200) { return }
        }
        catch {
            Write-Host "Waiting for Shicha Qianji API: $($_.Exception.Message)" -ForegroundColor Yellow
        }
        Start-Sleep -Seconds 3
    } while ((Get-Date) -lt $deadline)

    throw "Shicha Qianji API did not become ready within 2 minutes. Check $apiStderrPath."
}

function Wait-ShichaQianjiFrontend {
    $deadline = (Get-Date).AddMinutes(1)
    do {
        try {
            $response = Invoke-WebRequest -UseBasicParsing `
                -Uri "http://127.0.0.1:5173" -TimeoutSec 8
            if ($response.StatusCode -eq 200) { return }
        }
        catch {
            Write-Host "Waiting for Shicha Qianji frontend: $($_.Exception.Message)" `
                -ForegroundColor Yellow
        }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)

    throw "Shicha Qianji frontend did not become ready within 1 minute. Check $frontendStderrPath."
}

function Start-ShichaQianjiApi {
    $tracked = Test-TrackedProcess $apiPidPath @("api_main.py") @("uv", "python")
    if ($tracked) {
        Write-Host "时察千机后端已运行（PID $($tracked.Id)）。" -ForegroundColor Green
        return
    }

    $listener = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
    if ($listener) {
        $listenerCommandLine = Get-ProcessCommandLine ([int]$listener.OwningProcess)
        if ($listenerCommandLine -match "api_main\.py") {
            $rootPid = Get-ApiRootProcessId ([int]$listener.OwningProcess)
            Set-Content -LiteralPath $apiPidPath -Value $rootPid -Encoding ASCII
            Write-Host "时察千机后端已运行，已接管现有 PID $rootPid。" -ForegroundColor Green
        }
        else {
            Write-Host "端口 8000 已被其他进程占用，跳过后端启动并等待健康检查。" `
                -ForegroundColor Yellow
        }
        return
    }

    $process = Start-Process -FilePath $uvPath `
        -ArgumentList @("run", "python", "api_main.py") `
        -WorkingDirectory $projectRoot -WindowStyle Hidden `
        -RedirectStandardOutput $apiStdoutPath `
        -RedirectStandardError $apiStderrPath -PassThru
    Set-Content -LiteralPath $apiPidPath -Value $process.Id -Encoding ASCII
    Write-Host "时察千机后端已启动（PID $($process.Id)）。" -ForegroundColor Green
}

function Start-ShichaQianjiFrontend {
    $tracked = Test-TrackedProcess $frontendPidPath @("vite", "frontend") @("node")
    if ($tracked) {
        Write-Host "Vue3 运维工作台已运行（PID $($tracked.Id)）。" -ForegroundColor Green
        return
    }

    $listener = Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue
    if ($listener) {
        $listenerCommandLine = Get-ProcessCommandLine ([int]$listener.OwningProcess)
        if ($listenerCommandLine -match "vite" -and $listenerCommandLine -match "frontend") {
            Set-Content -LiteralPath $frontendPidPath `
                -Value ([int]$listener.OwningProcess) -Encoding ASCII
            Write-Host "Vue3 运维工作台已运行，已接管现有 PID $($listener.OwningProcess)。" `
                -ForegroundColor Green
            return
        }
        throw "端口 5173 已被非本项目 Vite 进程占用，无法启动 Vue3 运维工作台。"
    }

    $viteEntryPath = Join-Path $frontendRoot "node_modules\vite\bin\vite.js"
    if (-not (Test-Path -LiteralPath $viteEntryPath -PathType Leaf)) {
        throw "前端依赖未安装。请先在 frontend 目录运行 npm install，然后重新执行启动脚本。"
    }

    $nodeCandidates = @(
        "E:\Tools\nodejs\node.exe",
        "C:\Program Files\nodejs\node.exe"
    )
    $nodePath = $nodeCandidates |
        Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
        Select-Object -First 1
    if (-not $nodePath) {
        $nodeCommand = Get-Command "node.exe" -ErrorAction SilentlyContinue
        if ($nodeCommand) {
            $nodePath = $nodeCommand.Source
        }
    }
    if (-not $nodePath) {
        throw "未找到 Node.js，无法启动 Vue3 运维工作台。"
    }

    $process = Start-Process -FilePath $nodePath `
        -ArgumentList @(
            $viteEntryPath, "--host", "127.0.0.1", "--port", "5173", "--strictPort"
        ) `
        -WorkingDirectory $frontendRoot -WindowStyle Hidden `
        -RedirectStandardOutput $frontendStdoutPath `
        -RedirectStandardError $frontendStderrPath -PassThru
    Set-Content -LiteralPath $frontendPidPath -Value $process.Id -Encoding ASCII
    Start-Sleep -Seconds 2

    if (-not (Test-TrackedProcess $frontendPidPath @("vite", "frontend") @("node"))) {
        $errorText = Get-Content -LiteralPath $frontendStderrPath -Raw `
            -ErrorAction SilentlyContinue
        throw "Vue3 运维工作台启动后退出。$errorText"
    }
    Write-Host "Vue3 运维工作台已启动（PID $($process.Id)）。" -ForegroundColor Green
}

function Start-WorkflowTrigger($definition) {
    $tracked = Test-TrackedProcess $definition.PidPath @(
        "trigger_wanwu_workflow.ps1",
        [System.IO.Path]::GetFileName($definition.ConfigPath)
    )
    if ($tracked) {
        Write-Host "$($definition.Label)触发器已运行（PID $($tracked.Id)）。" -ForegroundColor Green
        return
    }

    if ($definition.Name -eq "autonomous") {
        $legacyPidPath = Join-Path $outputDirectory "wanwu_trigger.pid"
        $legacyTracked = Test-TrackedProcess $legacyPidPath @(
            "trigger_wanwu_workflow.ps1",
            [System.IO.Path]::GetFileName($definition.ConfigPath)
        ) @("powershell", "pwsh")
        if ($legacyTracked) {
            Set-Content -LiteralPath $definition.PidPath -Value $legacyTracked.Id -Encoding ASCII
            Remove-Item -LiteralPath $legacyPidPath -Force -ErrorAction SilentlyContinue
            Write-Host "$($definition.Label)触发器已接管旧版进程（PID $($legacyTracked.Id)）。" `
                -ForegroundColor Green
            return
        }
    }

    $process = Start-Process -FilePath "powershell.exe" `
        -ArgumentList @(
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $triggerScript,
            "-ConfigPath", $definition.ConfigPath
        ) `
        -WorkingDirectory $projectRoot -WindowStyle Hidden `
        -RedirectStandardOutput $definition.StdoutPath `
        -RedirectStandardError $definition.StderrPath -PassThru
    Set-Content -LiteralPath $definition.PidPath -Value $process.Id -Encoding ASCII
    Start-Sleep -Seconds 3

    if (-not (Test-TrackedProcess $definition.PidPath @(
        "trigger_wanwu_workflow.ps1",
        [System.IO.Path]::GetFileName($definition.ConfigPath)
    ) @("powershell", "pwsh"))) {
        $errorText = Get-Content -LiteralPath $definition.StderrPath -Raw -ErrorAction SilentlyContinue
        throw "$($definition.Label)触发器启动后退出。$errorText"
    }
    Write-Host "$($definition.Label)触发器已启动（PID $($process.Id)）。" -ForegroundColor Green
}

New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
if (-not (Test-Path -LiteralPath $wanwuRoot -PathType Container)) {
    throw "Wanwu source directory does not exist: $wanwuRoot"
}
if (-not $SkipTrigger) {
    Assert-WorkflowConfigs
}

Write-Host "[1/8] Checking PostgreSQL service..." -ForegroundColor Cyan
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

Write-Host "[2/8] Checking Docker engine..." -ForegroundColor Cyan
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

Write-Host "[3/8] Starting Wanwu basic services (ontology excluded)..." -ForegroundColor Cyan
Push-Location $wanwuRoot
try {
    docker compose @composeArgs up -d $baseServices
}
finally {
    Pop-Location
}

Write-Host "[4/8] Waiting for Wanwu upstreams and refreshing Nginx routing..." -ForegroundColor Cyan
Wait-WanwuUpstreams
docker restart nginx-wanwu | Out-Null
Wait-WanwuGateway
Write-Host "Wanwu gateway ready: http://127.0.0.1:8081" -ForegroundColor Green

if (-not $SkipApi) {
    Write-Host "[5/8] Starting Shicha Qianji API on port 8000..." -ForegroundColor Cyan
    Start-ShichaQianjiApi
    Wait-ShichaQianjiApi
    Write-Host "Shicha Qianji API ready: http://127.0.0.1:8000" -ForegroundColor Green
}
else {
    Write-Host "[5/8] Skipping Shicha Qianji API by request." -ForegroundColor Yellow
}

if (-not $SkipTrigger) {
    Write-Host "[6/8] Starting four Wanwu workflow triggers..." -ForegroundColor Cyan
    foreach ($definition in $workflowDefinitions) {
        Start-WorkflowTrigger $definition
    }
}
else {
    Write-Host "[6/8] Skipping workflow triggers by request." -ForegroundColor Yellow
}

if ($startFrontend) {
    Write-Host "[7/8] Starting Vue3 competition console on port 5173..." -ForegroundColor Cyan
    Start-ShichaQianjiFrontend
    Wait-ShichaQianjiFrontend
    Write-Host "Vue3 competition console ready: http://127.0.0.1:5173" -ForegroundColor Green
}
else {
    Write-Host "[7/8] Skipping Vue3 competition console by request." -ForegroundColor Yellow
}

Write-Host "[8/8] Basic competition stack is ready." -ForegroundColor Green
Write-Host "Wanwu: http://127.0.0.1:8081" -ForegroundColor Green
Write-Host "Shicha Qianji API: http://127.0.0.1:8000" -ForegroundColor Green
if ($startFrontend) {
    Write-Host "Vue3 console: http://127.0.0.1:5173" -ForegroundColor Green
}
Write-Host "Run scripts/check_basic_stack.ps1 and scripts/accept_wanwu_workflows.ps1 for read-only checks." `
    -ForegroundColor DarkGray
