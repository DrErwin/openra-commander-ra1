$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$operator = Join-Path $projectRoot 'commander\scripts\live_operator.py'

if (-not (Test-Path -LiteralPath $python)) {
    throw '未找到项目 Python 环境。'
}

Push-Location $projectRoot
try {
    & $python $operator stop
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
