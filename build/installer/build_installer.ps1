param(
    [string]$Iscc = "",
    [string]$CreatorDir = "",
    [string]$OutputDir = "",
    [ValidateSet("Fast", "Release")]
    [string]$CompressionProfile = "Release"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$iconFile = Join-Path $repoRoot "build\branding\KeyWave.ico"
if ([string]::IsNullOrWhiteSpace($CreatorDir)) {
    $CreatorDir = Join-Path $repoRoot "artifacts\creator\keywave_creator.dist"
}
if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $repoRoot "artifacts\installer"
}
if ([string]::IsNullOrWhiteSpace($Iscc)) {
    $command = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        $Iscc = $command.Source
    }
    else {
        $candidate = Join-Path ${env:LOCALAPPDATA} "Programs\Inno Setup 6\ISCC.exe"
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $Iscc = $candidate
        }
    }
}

$creatorExecutable = Join-Path $CreatorDir "KeyWaveCreator.exe"
if (-not (Test-Path -LiteralPath $creatorExecutable -PathType Leaf)) {
    throw "The Creator build is missing: $creatorExecutable"
}
if (-not (Test-Path -LiteralPath $iconFile -PathType Leaf)) {
    throw "The KeyWave application icon is missing: $iconFile"
}
if ([string]::IsNullOrWhiteSpace($Iscc) -or -not (Test-Path -LiteralPath $Iscc -PathType Leaf)) {
    throw "Inno Setup 6 ISCC.exe was not found. Install JRSoftware.InnoSetup or pass -Iscc."
}

New-Item -ItemType Directory -Force $OutputDir | Out-Null
$version = (Get-Content -LiteralPath (Join-Path $repoRoot "VERSION") -Raw).Trim()
$expectedInstaller = Join-Path $OutputDir "KeyWave-Creator-$version-windows-x64-setup.exe"
if (Test-Path -LiteralPath $expectedInstaller) {
    Remove-Item -LiteralPath $expectedInstaller -Force
}
$compressionMethod = if ($CompressionProfile -eq "Fast") { "lzma2/fast" } else { "lzma2/max" }
$solidCompression = if ($CompressionProfile -eq "Fast") { "no" } else { "yes" }
& $Iscc `
    "/DAppVersion=$version" `
    "/DCreatorDir=$([System.IO.Path]::GetFullPath($CreatorDir))" `
    "/DOutputDir=$([System.IO.Path]::GetFullPath($OutputDir))" `
    "/DIconFile=$([System.IO.Path]::GetFullPath($iconFile))" `
    "/DCompressionMethod=$compressionMethod" `
    "/DSolidCompressionValue=$solidCompression" `
    (Join-Path $PSScriptRoot "KeyWaveCreator.iss")
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup failed with exit code $LASTEXITCODE."
}
if (-not (Test-Path -LiteralPath $expectedInstaller -PathType Leaf)) {
    throw "Inno Setup did not create the expected installer: $expectedInstaller"
}
Write-Output "KeyWave Creator installer created: $expectedInstaller"
