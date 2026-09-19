[CmdletBinding()]
param(
    [switch]$Headless
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$operator = Join-Path $projectRoot 'commander\scripts\live_operator.py'
$game = Join-Path $projectRoot 'OpenRA\bin\OpenRA.exe'

if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $game)) {
    throw 'RA1 尚未准备完成。请先运行 .\setup-ra1.ps1。'
}

$startArguments = @($operator, 'start')
if ($Headless) {
    $startArguments += '--headless'
}

Push-Location $projectRoot
try {
    & $python @startArguments
    if ($LASTEXITCODE -ne 0) {
        throw 'RA1 游戏会话启动失败。查看 commander\evidence\live-operator.log 获取原因。'
    }

    Write-Host ''
    Write-Host 'RA1 已启动。下面是 AI 客户端需要使用的 MCP 配置：' -ForegroundColor Green
    & $python $operator mcp-config
    Write-Host ''
    Write-Host '把这段配置加入 Codex/Claude 等 MCP 客户端后，即可让 AI 读取战场并下达任务。'
    Write-Host '停止游戏：.\stop-ra1.ps1'
}
finally {
    Pop-Location
}
