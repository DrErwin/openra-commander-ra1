<##
Launch the project-owned, unmodified OpenRA runtime for a visible vanilla game.

This script intentionally does not look at any external reference checkout.
The runtime is expected at ..\..\vendor\openra-official and can be replaced
by the setup/build process described in the project documentation.
##>

[CmdletBinding()]
param(
    [string]$Mod = "ra",
    [string]$Map,
    [string]$SupportDir
)

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$engineRoot = Join-Path $projectRoot "vendor\openra-official"
$executable = Join-Path $engineRoot "bin\OpenRA.exe"

if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "Project-owned OpenRA runtime not found: $executable. Build or install it under vendor\openra-official first."
}

if ([string]::IsNullOrWhiteSpace($SupportDir)) {
    $SupportDir = Join-Path $projectRoot "commander\runtime\vanilla-official-user"
}
New-Item -ItemType Directory -Force -Path $SupportDir | Out-Null

$arguments = @(
    "Engine.EngineDir=$engineRoot"
    "Game.Mod=$Mod"
    "Engine.SupportDir=$SupportDir"
)
if (-not [string]::IsNullOrWhiteSpace($Map)) {
    $arguments += "Launch.Map=$Map"
}

$argumentLine = ($arguments | ForEach-Object {
    '"' + ($_ -replace '"', '\"') + '"'
}) -join ' '
Start-Process -FilePath $executable -ArgumentList $argumentLine -WorkingDirectory $engineRoot -WindowStyle Normal
Write-Host "Started official OpenRA from $engineRoot"
Write-Host "Support data: $SupportDir"
