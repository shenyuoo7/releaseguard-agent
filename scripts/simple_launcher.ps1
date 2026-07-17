[CmdletBinding()]
param(
    [ValidateSet('Menu', 'Check', 'Web', 'Verify', 'Demo', 'RepairEnvironment', 'Serve')]
    [string]$Action = 'Menu',
    [string]$ProjectPath,
    [string]$BeforePath,
    [string]$AfterPath,
    [string]$OutputRoot,
    [string]$RuntimeRoot,
    [ValidateRange(1, 65535)]
    [int]$Port = 8000,
    [switch]$NoBrowser,
    [switch]$NoPause,
    [switch]$Json,
    [switch]$TestMode
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:ProjectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$script:PythonPath = Join-Path $script:ProjectRoot '.venv\Scripts\python.exe'
$script:RuntimeRoot = if ($RuntimeRoot) {
    [System.IO.Path]::GetFullPath($RuntimeRoot)
} else {
    Join-Path $script:ProjectRoot '.runtime'
}

function Initialize-ReleaseGuardEnvironment {
    $projectDrive = [System.IO.Path]::GetPathRoot($script:ProjectRoot)
    $runtimeDrive = [System.IO.Path]::GetPathRoot($script:RuntimeRoot)
    if ($projectDrive -ne 'E:\' -or $runtimeDrive -ne 'E:\') {
        throw 'ReleaseGuard 项目、缓存和临时目录必须位于 E 盘。'
    }

    $tempRoot = Join-Path $script:RuntimeRoot 'temp'
    $pipCache = Join-Path $script:RuntimeRoot 'pip-cache'
    $pytestRoot = Join-Path $script:RuntimeRoot 'pytest'
    $pytestRunName = '{0}-{1}' -f (Get-Date).ToString('yyyyMMdd-HHmmss-fff'), ([guid]::NewGuid().ToString('N').Substring(0, 8))
    $pytestBaseTemp = Join-Path $pytestRoot $pytestRunName
    foreach ($directory in @($tempRoot, $pipCache, $pytestRoot)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }
    New-Item -ItemType Directory -Path $pytestBaseTemp | Out-Null

    $env:PYTHONPATH = Join-Path $script:ProjectRoot 'src'
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $env:TEMP = $tempRoot
    $env:TMP = $tempRoot
    $env:TMPDIR = $tempRoot
    $env:PIP_CACHE_DIR = $pipCache
    $env:PYTEST_ADDOPTS = "-p no:cacheprovider --basetemp=$pytestBaseTemp"

    return [pscustomobject]@{
        temp_root = $tempRoot
        pip_cache = $pipCache
        pytest_basetemp = $pytestBaseTemp
    }
}

function Assert-ReleaseGuardPython {
    if (-not (Test-Path -LiteralPath $script:PythonPath -PathType Leaf)) {
        throw "找不到项目虚拟环境：$script:PythonPath`n请返回主菜单选择 5 安装或修复运行环境。"
    }
}

function ConvertTo-ProjectDirectory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value,
        [string]$Label = '项目目录'
    )

    $normalized = [Environment]::ExpandEnvironmentVariables($Value.Trim().Trim('"').Trim("'"))
    if ([string]::IsNullOrWhiteSpace($normalized)) {
        throw "$Label 不能为空。"
    }
    if (-not (Test-Path -LiteralPath $normalized -PathType Container)) {
        throw "$Label 不存在：$normalized"
    }
    return (Get-Item -LiteralPath $normalized).FullName
}

function New-RunOutputDirectory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$BaseDirectory
    )

    $runName = '{0}-{1}' -f (Get-Date).ToString('yyyyMMdd-HHmmss-fff'), ([guid]::NewGuid().ToString('N').Substring(0, 8))
    $runDirectory = Join-Path $BaseDirectory $runName
    New-Item -ItemType Directory -Path $runDirectory | Out-Null
    return $runDirectory
}

function Invoke-ReleaseGuardCli {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [int[]]$AcceptedExitCodes = @(0)
    )

    Assert-ReleaseGuardPython
    $output = (& $script:PythonPath -m releaseguard_agent.cli.main @Arguments 2>&1 | Out-String).Trim()
    $exitCode = $LASTEXITCODE
    if ($exitCode -notin $AcceptedExitCodes) {
        $detail = if ($output) { $output } else { '命令没有返回详细错误。' }
        throw "ReleaseGuard 命令执行失败（退出码 $exitCode）：`n$detail"
    }
    return [pscustomobject]@{
        exit_code = $exitCode
        output = $output
    }
}

function Invoke-QuickReview {
    param(
        [Parameter(Mandatory = $true)]
        [string]$TargetPath,
        [string]$BaseOutputDirectory
    )

    $target = ConvertTo-ProjectDirectory -Value $TargetPath
    $base = if ($BaseOutputDirectory) {
        [System.IO.Path]::GetFullPath($BaseOutputDirectory)
    } else {
        Join-Path $script:ProjectRoot 'outputs\latest_review'
    }
    $runDirectory = New-RunOutputDirectory -BaseDirectory $base
    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    if (-not $Json) {
        Write-Host ''
        Write-Host "正在检查：$target" -ForegroundColor Cyan
        Write-Host '项目较大时可能需要几十秒，请不要关闭窗口……'
    }
    $command = Invoke-ReleaseGuardCli -AcceptedExitCodes @(0, 1) -Arguments @(
        'check', $target,
        '--skip-pytest-execution',
        '--format', 'json',
        '--output-dir', $runDirectory,
        '--checklist-output-dir', $runDirectory,
        '--agent-advice-output-dir', $runDirectory,
        '--trace-output-dir', $runDirectory
    )
    $stopwatch.Stop()
    $payload = $command.output | ConvertFrom-Json
    $issueCount = [int]$payload.summary.failed + [int]$payload.summary.warning
    return [pscustomobject]@{
        action = 'check'
        project_root = $script:ProjectRoot
        runtime_root = $script:RuntimeRoot
        project_path = $target
        release_allowed = [bool]($payload.summary.blocking -eq 0)
        issue_count = $issueCount
        blocking_count = [int]$payload.summary.blocking
        elapsed_seconds = [math]::Round($stopwatch.Elapsed.TotalSeconds, 2)
        llm_used = $false
        mode = 'deterministic_offline'
        report_path = Join-Path $runDirectory 'release_report.md'
        checklist_path = Join-Path $runDirectory 'release_checklist.md'
        trace_path = Join-Path $runDirectory 'trace.json'
        output_directory = $runDirectory
    }
}

function Invoke-BeforeAfterVerification {
    param(
        [Parameter(Mandatory = $true)]
        [string]$BaselinePath,
        [Parameter(Mandatory = $true)]
        [string]$ModifiedPath,
        [string]$BaseOutputDirectory
    )

    $before = ConvertTo-ProjectDirectory -Value $BaselinePath -Label '修复前项目目录'
    $after = ConvertTo-ProjectDirectory -Value $ModifiedPath -Label '修复后项目目录'
    $base = if ($BaseOutputDirectory) {
        [System.IO.Path]::GetFullPath($BaseOutputDirectory)
    } else {
        Join-Path $script:ProjectRoot 'outputs\latest_verification'
    }
    $runDirectory = New-RunOutputDirectory -BaseDirectory $base
    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    if (-not $Json) {
        Write-Host ''
        Write-Host '正在重新扫描并比较修复前后结果，请稍候……' -ForegroundColor Cyan
    }
    $command = Invoke-ReleaseGuardCli -AcceptedExitCodes @(0, 1) -Arguments @(
        'verify', $before, $after,
        '--skip-pytest-execution',
        '--execution-trace-output-dir', $runDirectory
    )
    $stopwatch.Stop()
    $payload = $command.output | ConvertFrom-Json
    $resultPath = Join-Path $runDirectory 'verification_result.json'
    $command.output | Set-Content -LiteralPath $resultPath -Encoding UTF8
    return [pscustomobject]@{
        action = 'verify'
        project_root = $script:ProjectRoot
        runtime_root = $script:RuntimeRoot
        before_path = $before
        after_path = $after
        resolved_count = @($payload.delta.resolved).Count
        new_count = @($payload.delta.new).Count
        unchanged_count = @($payload.delta.unchanged).Count
        release_allowed = [bool]$payload.delta.release_allowed
        status = [string]$payload.delta.status
        elapsed_seconds = [math]::Round($stopwatch.Elapsed.TotalSeconds, 2)
        llm_used = $false
        mode = 'deterministic_offline'
        result_path = $resultPath
        trace_path = Join-Path $runDirectory 'execution_trace.json'
        output_directory = $runDirectory
    }
}

function Get-ReleaseGuardHealth {
    param([int]$HealthPort)
    try {
        $response = Invoke-RestMethod -Uri "http://127.0.0.1:$HealthPort/health" -TimeoutSec 2
        if ($response.status -eq 'ok' -and $response.service -eq 'releaseguard-agent') {
            return $response
        }
    } catch {
        return $null
    }
    return $null
}

function Test-LocalPortInUse {
    param([int]$LocalPort)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect('127.0.0.1', $LocalPort, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne(400)) {
            return $false
        }
        $client.EndConnect($async)
        return $true
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

function Start-ReleaseGuardWeb {
    param(
        [int]$WebPort = 8000,
        [switch]$SkipBrowser,
        [switch]$StopAfterCheck
    )

    Assert-ReleaseGuardPython
    $existingHealth = Get-ReleaseGuardHealth -HealthPort $WebPort
    if ($null -ne $existingHealth) {
        if (-not $SkipBrowser) {
            Start-Process "http://127.0.0.1:$WebPort/docs"
        }
        return [pscustomobject]@{
            action = 'web'
            project_root = $script:ProjectRoot
            runtime_root = $script:RuntimeRoot
            status = 'already_running'
            url = "http://127.0.0.1:$WebPort/docs"
            health = 'ok'
        }
    }
    if (Test-LocalPortInUse -LocalPort $WebPort) {
        throw "端口 $WebPort 已被其他程序占用。请关闭占用程序后重试。"
    }

    if ($StopAfterCheck) {
        $process = Start-Process -FilePath $script:PythonPath -ArgumentList @(
            '-m', 'uvicorn', 'releaseguard_agent.api.app:app',
            '--host', '127.0.0.1', '--port', $WebPort
        ) -WorkingDirectory $script:ProjectRoot -WindowStyle Hidden -PassThru
    } else {
        $quotedScript = '"{0}"' -f $PSCommandPath
        $process = Start-Process -FilePath 'powershell.exe' -ArgumentList @(
            '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass',
            '-File', $quotedScript, '-Action', 'Serve', '-Port', $WebPort
        ) -WorkingDirectory $script:ProjectRoot -PassThru
    }

    $deadline = (Get-Date).AddSeconds(30)
    $health = $null
    try {
        do {
            Start-Sleep -Milliseconds 500
            $health = Get-ReleaseGuardHealth -HealthPort $WebPort
            if ($null -ne $health) { break }
            if ($process.HasExited) {
                throw '网页服务进程提前退出。请检查虚拟环境和依赖。'
            }
        } while ((Get-Date) -lt $deadline)
        if ($null -eq $health) {
            throw '网页服务在 30 秒内未通过健康检查。'
        }
        if (-not $SkipBrowser) {
            Start-Process "http://127.0.0.1:$WebPort/docs"
        }
        return [pscustomobject]@{
            action = 'web'
            project_root = $script:ProjectRoot
            runtime_root = $script:RuntimeRoot
            status = 'started'
            url = "http://127.0.0.1:$WebPort/docs"
            health = [string]$health.status
            process_id = $process.Id
        }
    } finally {
        if ($StopAfterCheck -and -not $process.HasExited) {
            Stop-Process -Id $process.Id -Force
            $process.WaitForExit()
        }
    }
}

function Invoke-BuiltInDemo {
    param([string]$BaseOutputDirectory)
    $base = if ($BaseOutputDirectory) {
        [System.IO.Path]::GetFullPath($BaseOutputDirectory)
    } else {
        Join-Path $script:ProjectRoot 'outputs\latest_demo'
    }
    $runDirectory = New-RunOutputDirectory -BaseDirectory $base
    if (-not $Json) {
        Write-Host ''
        Write-Host '正在运行 clean、blocking 和修复对比演示，请稍候……' -ForegroundColor Cyan
    }
    $clean = Invoke-QuickReview -TargetPath (Join-Path $script:ProjectRoot 'sample_projects\clean_python_project') -BaseOutputDirectory (Join-Path $runDirectory 'clean')
    $blocking = Invoke-QuickReview -TargetPath (Join-Path $script:ProjectRoot 'sample_projects\fastapi_bad_project') -BaseOutputDirectory (Join-Path $runDirectory 'blocking')
    $verification = Invoke-BeforeAfterVerification -BaselinePath (Join-Path $script:ProjectRoot 'sample_projects\fastapi_bad_project') -ModifiedPath (Join-Path $script:ProjectRoot 'sample_projects\fastapi_good_project') -BaseOutputDirectory (Join-Path $runDirectory 'verification')
    return [pscustomobject]@{
        action = 'demo'
        project_root = $script:ProjectRoot
        runtime_root = $script:RuntimeRoot
        clean_release_allowed = $clean.release_allowed
        blocking_release_allowed = $blocking.release_allowed
        resolved_count = $verification.resolved_count
        new_count = $verification.new_count
        unchanged_count = $verification.unchanged_count
        verification_release_allowed = $verification.release_allowed
        output_directory = $runDirectory
    }
}

function Repair-ReleaseGuardEnvironment {
    $launcher = Get-Command 'py.exe' -ErrorAction SilentlyContinue
    if (-not (Test-Path -LiteralPath $script:PythonPath -PathType Leaf)) {
        if ($null -eq $launcher) {
            throw '未找到 Python Launcher。请先安装 Python 3.11 或更高版本，并勾选 py launcher。'
        }
        $versionOutput = (& $launcher.Source -3.11 --version 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or $versionOutput -notmatch '^Python 3\.11(?:\.|$)') {
            throw '未找到 Python 3.11。请安装 Python 3.11 后重试。'
        }
        & $launcher.Source -3.11 -m venv (Join-Path $script:ProjectRoot '.venv')
        if ($LASTEXITCODE -ne 0) {
            throw '创建项目 .venv 失败。现有目录不会被自动删除。'
        }
    }

    Assert-ReleaseGuardPython
    $pythonVersionOutput = (& $script:PythonPath --version 2>&1 | Out-String).Trim()
    $pythonVersion = $pythonVersionOutput -replace '^Python\s+', ''
    $parts = $pythonVersion.Split('.')
    if ($parts.Count -lt 2 -or [int]$parts[0] -lt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -lt 11)) {
        throw "项目虚拟环境 Python 版本过低：$pythonVersion。需要 Python 3.11 或更高版本。"
    }
    $previousErrorPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $pipOutput = & $script:PythonPath -m pip install -r (Join-Path $script:ProjectRoot 'requirements.txt') -r (Join-Path $script:ProjectRoot 'requirements-dev.txt') 2>&1
        $pipExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorPreference
    }
    if (-not $Json) {
        $pipOutput | ForEach-Object { Write-Host $_ }
    }
    if ($pipExitCode -ne 0) {
        $detail = ($pipOutput | Out-String).Trim()
        throw "依赖安装失败。请检查网络、代理和 pip 错误：`n$detail"
    }
    $health = Invoke-ReleaseGuardCli -Arguments @('list-checkers', '--skip-pytest-execution')
    return [pscustomobject]@{
        action = 'repair_environment'
        project_root = $script:ProjectRoot
        runtime_root = $script:RuntimeRoot
        python_version = $pythonVersion
        checker_count = @($health.output -split "`r?`n" | Where-Object { $_ }).Count
        status = 'ok'
    }
}

function Write-ActionResult {
    param([Parameter(Mandatory = $true)]$Result)
    if ($Json) {
        $Result | ConvertTo-Json -Depth 8 -Compress
        return
    }
    switch ($Result.action) {
        'check' {
            Write-Host ''
            Write-Host ('是否允许发布：{0}' -f $(if ($Result.release_allowed) { '是' } else { '否' }))
            Write-Host "发现问题：$($Result.issue_count)"
            Write-Host "阻断问题：$($Result.blocking_count)"
            Write-Host "耗时：$($Result.elapsed_seconds) 秒"
            Write-Host "报告位置：$($Result.report_path)"
            Write-Host "清单位置：$($Result.checklist_path)"
            Write-Host "Trace位置：$($Result.trace_path)"
            Write-Host '运行模式：确定性离线检查（本次未调用 LLM）'
            Write-Host '如需真实 LLM 智能分析，必须安全配置 API Key 并显式启用。'
        }
        'verify' {
            Write-Host ''
            Write-Host "已解决问题：$($Result.resolved_count)"
            Write-Host "新增问题：$($Result.new_count)"
            Write-Host "未解决问题：$($Result.unchanged_count)"
            Write-Host "耗时：$($Result.elapsed_seconds) 秒"
            Write-Host ('最终是否允许发布：{0}' -f $(if ($Result.release_allowed) { '是' } else { '否' }))
            Write-Host "详细结果目录：$($Result.output_directory)"
        }
        'web' {
            Write-Host ''
            Write-Host '网页界面已启动。'
            Write-Host "地址：$($Result.url)"
            if ($Result.status -eq 'started' -and -not $TestMode) {
                Write-Host '关闭新打开的服务窗口即可停止服务。'
            }
        }
        'demo' {
            Write-Host ''
            Write-Host ('Clean项目允许发布：{0}' -f $(if ($Result.clean_release_allowed) { '是' } else { '否' }))
            Write-Host ('Blocking项目允许发布：{0}' -f $(if ($Result.blocking_release_allowed) { '是' } else { '否' }))
            Write-Host "修复对比：已解决 $($Result.resolved_count)，新增 $($Result.new_count)，未解决 $($Result.unchanged_count)"
            Write-Host "详细报告位置：$($Result.output_directory)"
        }
        'repair_environment' {
            Write-Host ''
            Write-Host "运行环境正常。Python $($Result.python_version)，已发现 $($Result.checker_count) 个检查器。"
        }
    }
}

function Wait-ForMenuReturn {
    if (-not $NoPause) {
        Write-Host ''
        [void](Read-Host '按回车键返回主菜单')
    }
}

function Show-MainMenu {
    while ($true) {
        Clear-Host
        Write-Host '================================'
        Write-Host 'ReleaseGuard 发布检查助手'
        Write-Host '================================'
        Write-Host '当前默认：确定性离线模式（不调用 LLM）'
        Write-Host '真实 LLM 智能分析需要 API Key，并且必须显式启用。'
        Write-Host '--------------------------------'
        Write-Host '1. 快速检查一个项目'
        Write-Host '2. 启动网页界面'
        Write-Host '3. 对比修复前后项目'
        Write-Host '4. 运行自带演示'
        Write-Host '5. 安装或修复运行环境'
        Write-Host '0. 退出'
        Write-Host '================================'
        $choice = Read-Host '请选择'
        try {
            switch ($choice) {
                '1' {
                    $path = Read-Host '请粘贴需要检查的项目文件夹路径'
                    Write-ActionResult (Invoke-QuickReview -TargetPath $path)
                    Wait-ForMenuReturn
                }
                '2' {
                    Write-ActionResult (Start-ReleaseGuardWeb -WebPort 8000)
                    Wait-ForMenuReturn
                }
                '3' {
                    $before = Read-Host '请输入修复前项目文件夹路径'
                    $after = Read-Host '请输入修复后项目文件夹路径'
                    Write-ActionResult (Invoke-BeforeAfterVerification -BaselinePath $before -ModifiedPath $after)
                    Wait-ForMenuReturn
                }
                '4' {
                    Write-ActionResult (Invoke-BuiltInDemo)
                    Wait-ForMenuReturn
                }
                '5' {
                    Write-ActionResult (Repair-ReleaseGuardEnvironment)
                    Wait-ForMenuReturn
                }
                '0' { return }
                default {
                    Write-Host '请输入 0 到 5 之间的数字。' -ForegroundColor Yellow
                    Wait-ForMenuReturn
                }
            }
        } catch {
            Write-Host ''
            Write-Host "[错误] $($_.Exception.Message)" -ForegroundColor Red
            Write-Host '没有读取 .env，也没有调用真实 LLM。请按提示修正后重试。'
            Wait-ForMenuReturn
        }
    }
}

$environmentInfo = Initialize-ReleaseGuardEnvironment
try {
    switch ($Action) {
        'Menu' { Show-MainMenu }
        'Check' {
            if (-not $ProjectPath) { throw '必须通过 -ProjectPath 提供项目目录。' }
            Write-ActionResult (Invoke-QuickReview -TargetPath $ProjectPath -BaseOutputDirectory $OutputRoot)
        }
        'Web' {
            Write-ActionResult (Start-ReleaseGuardWeb -WebPort $Port -SkipBrowser:$NoBrowser -StopAfterCheck:$TestMode)
        }
        'Verify' {
            if (-not $BeforePath -or -not $AfterPath) { throw '必须同时提供 -BeforePath 和 -AfterPath。' }
            Write-ActionResult (Invoke-BeforeAfterVerification -BaselinePath $BeforePath -ModifiedPath $AfterPath -BaseOutputDirectory $OutputRoot)
        }
        'Demo' { Write-ActionResult (Invoke-BuiltInDemo -BaseOutputDirectory $OutputRoot) }
        'RepairEnvironment' { Write-ActionResult (Repair-ReleaseGuardEnvironment) }
        'Serve' {
            Assert-ReleaseGuardPython
            & $script:PythonPath -m uvicorn releaseguard_agent.api.app:app --host 127.0.0.1 --port $Port
            exit $LASTEXITCODE
        }
    }
    exit 0
} catch {
    if ($Json) {
        [pscustomobject]@{
            status = 'error'
            message = $_.Exception.Message
            project_root = $script:ProjectRoot
            runtime_root = $script:RuntimeRoot
            temp_root = $environmentInfo.temp_root
        } | ConvertTo-Json -Compress
    } else {
        Write-Host "[错误] $($_.Exception.Message)" -ForegroundColor Red
        Write-Host '请根据提示修正后重试；程序未读取 .env 或任何 API Key。'
        if (-not $NoPause -and $Action -ne 'Menu') {
            [void](Read-Host '按回车键关闭')
        }
    }
    exit 2
}
