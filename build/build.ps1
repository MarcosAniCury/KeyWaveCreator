param(
    [ValidateSet("Development", "Release")]
    [string]$Configuration = "Development",
    [string]$Python = "",
    [string]$Iscc = "",
    [switch]$RunTests,
    [switch]$CreateInstaller
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if ([string]::IsNullOrWhiteSpace($Python)) {
    $Python = Join-Path $repoRoot ".venv\Scripts\python.exe"
}

if ($RunTests -or $Configuration -eq "Release") {
    Push-Location $repoRoot
    try {
        & $Python -m ruff format --check .
        if ($LASTEXITCODE -ne 0) { throw "Ruff formatting check failed." }
        & $Python -m ruff check .
        if ($LASTEXITCODE -ne 0) { throw "Ruff lint failed." }
        & $Python -m mypy
        if ($LASTEXITCODE -ne 0) { throw "mypy failed." }
        & $Python -m pytest
        if ($LASTEXITCODE -ne 0) { throw "pytest failed." }
    }
    finally {
        Pop-Location
    }
}

& (Join-Path $PSScriptRoot "build_creator.ps1") -Python $Python

if ($Configuration -eq "Release" -or $CreateInstaller) {
    $parameters = @{}
    if (-not [string]::IsNullOrWhiteSpace($Iscc)) {
        $parameters.Iscc = $Iscc
    }
    & (Join-Path $PSScriptRoot "installer\build_installer.ps1") @parameters
}
