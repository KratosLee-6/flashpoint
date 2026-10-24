param(
    [ValidateRange(1,65535)][int]$Port = 4178,
    [switch]$NoOpen,
    [switch]$Check
)

$ErrorActionPreference = 'Stop'
$appRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$workspaceRoot = Split-Path -Parent $appRoot
$appUrl = "http://127.0.0.1:$Port/app/"
$enteredWorkspace = $false
$launchExit = 0

function Test-SparkService {
    $response = $null
    try {
        $request = [System.Net.WebRequest]::Create("http://127.0.0.1:$Port/api/session")
        $request.Proxy = $null
        $request.Timeout = 1500
        $response = $request.GetResponse()
        $reader = New-Object System.IO.StreamReader($response.GetResponseStream())
        try { $payload = $reader.ReadToEnd() | ConvertFrom-Json } finally { $reader.Dispose() }
        return $payload.service -eq 'spark-fusion' -and $payload.protocol -eq 1
    } catch { return $false }
    finally { if ($null -ne $response) { $response.Dispose() } }
}

try {
    Push-Location -LiteralPath $workspaceRoot
    $enteredWorkspace = $true
    if (-not $Check -and (Test-SparkService)) {
        Write-Host "SPARK_APP_REUSED · 闪光点已在运行：$appUrl" -ForegroundColor Green
        if (-not $NoOpen) { Start-Process -FilePath $appUrl }
    } else {
        $pythonPath = $null
        $pythonPrefix = @()
        $probe = "import sys; assert sys.version_info >= (3,11), 'Python 3.11+ required'; from app.fusion.server import make_server; print('SPARK_PYTHON_READY')"
        foreach ($name in @('python.exe', 'py.exe')) {
            $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($null -eq $command) { continue }
            $prefix = @()
            if ($name -eq 'py.exe') { $prefix = @('-3') }
            try {
                $output = & $command.Source @prefix -X utf8 -c $probe 2>&1
                if ($LASTEXITCODE -eq 0 -and ($output -join "`n") -match 'SPARK_PYTHON_READY') {
                    $pythonPath = $command.Source
                    $pythonPrefix = $prefix
                    break
                }
            } catch { continue }
        }
        if ($null -eq $pythonPath) {
            throw '未找到可运行本项目的 Python 3.11+。请安装 Python 并勾选 Add Python to PATH，再重新启动。'
        }
        Write-Host "SPARK_PYTHON_READY · $pythonPath" -ForegroundColor Green
        if (-not $Check) {
            $probeListener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $Port)
            try { $probeListener.Start() }
            catch { throw "SPARK_PORT_IN_USE · 端口 $Port 已被其他服务占用。请保留现有服务，改用启动参数 -Port 4180。" }
            finally { $probeListener.Stop() }
            Write-Host "闪光点正在启动：$appUrl" -ForegroundColor Green
            Write-Host '请保持本窗口运行。关闭窗口会停止本机服务；不会自动调用模型。'
            $serverArgs = @('-X', 'utf8', '-m', 'app.fusion.server', '--port', "$Port")
            if (-not $NoOpen) { $serverArgs += '--open' }
            & $pythonPath @pythonPrefix @serverArgs
            if ($LASTEXITCODE -ne 0) { throw "本机服务退出，错误码 $LASTEXITCODE。请查看上方错误信息。" }
        }
    }
} catch {
    Write-Host "启动失败：$($_.Exception.Message)" -ForegroundColor Red
    $launchExit = 1
} finally {
    if ($enteredWorkspace) { Pop-Location }
}
exit $launchExit
