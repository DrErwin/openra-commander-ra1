[CmdletBinding()]
param(
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$previousLocation = Get-Location

try {
    Set-Location $projectRoot

    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw '未找到 Git。请先安装 Git for Windows。'
    }

    Write-Host '[1/4] 获取 RA1 引擎子模块...'
    git submodule sync --recursive
    if ($LASTEXITCODE -ne 0) { throw 'git submodule sync 失败。' }
    git submodule update --init --recursive
    if ($LASTEXITCODE -ne 0) { throw 'RA1 引擎子模块下载失败。' }

    Write-Host '[2/4] 创建 Python 环境并安装 AI 控制端...'
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        uv sync
        if ($LASTEXITCODE -ne 0) { throw 'uv sync 失败。' }
    }
    else {
        if (Get-Command py -ErrorAction SilentlyContinue) {
            py -3 -m venv .venv
        }
        elseif (Get-Command python -ErrorAction SilentlyContinue) {
            python -m venv .venv
        }
        else {
            throw '未找到 Python 3.10+。请安装 Python 后重试。'
        }

        if ($LASTEXITCODE -ne 0) { throw '创建 Python 虚拟环境失败。' }
        & .\.venv\Scripts\python.exe -m pip install --upgrade pip
        if ($LASTEXITCODE -ne 0) { throw '升级 pip 失败。' }
        & .\.venv\Scripts\python.exe -m pip install -e .
        if ($LASTEXITCODE -ne 0) { throw '安装 AI 控制端依赖失败。' }
    }

    if (-not $SkipBuild) {
        Write-Host '[3/4] 编译 RA1 游戏引擎...'
        if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
            throw '未找到 .NET 8 SDK。请安装 .NET 8 SDK 后重试。'
        }

        $dotnetVersion = dotnet --version
        if ($LASTEXITCODE -ne 0 -or [int]($dotnetVersion.Split('.')[0]) -lt 8) {
            throw "需要 .NET 8 SDK，当前版本为 $dotnetVersion。"
        }

        dotnet build .\OpenRA\OpenRA.sln -p:SKIP_PROTOC=true -v:minimal
        if ($LASTEXITCODE -ne 0) { throw 'RA1 游戏引擎编译失败。' }
    }
    else {
        Write-Host '[3/4] 已按参数跳过引擎编译。'
    }

    Write-Host '[4/4] 检查可玩文件...'
    $requiredFiles = @(
        '.\.venv\Scripts\python.exe',
        '.\OpenRA\bin\OpenRA.exe',
        '.\OpenRA\mods\ra\mod.yaml',
        '.\OpenRA\mods\ra\maps\agenda.oramap',
        '.\commander\scripts\live_operator.py'
    )

    foreach ($requiredFile in $requiredFiles) {
        if (-not (Test-Path -LiteralPath $requiredFile)) {
            throw "缺少可玩文件：$requiredFile"
        }
    }

    Write-Host ''
    Write-Host 'RA1 已准备完成。运行：' -ForegroundColor Green
    Write-Host '  .\play-ra1.ps1'
}
finally {
    Set-Location $previousLocation
}
