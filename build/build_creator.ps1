param(
    [string]$Python = "",
    [string]$OutputRoot = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$pythonRoot = $repoRoot
if ([string]::IsNullOrWhiteSpace($Python)) {
    $Python = Join-Path $pythonRoot ".venv\Scripts\python.exe"
}
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $repoRoot "artifacts\creator"
}
$OutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)
$cacheRoot = Join-Path $repoRoot "artifacts\download-cache"
$toolStage = Join-Path $repoRoot "artifacts\creator-tools"
New-Item -ItemType Directory -Force $cacheRoot, $toolStage, $OutputRoot | Out-Null

function Get-VerifiedFile {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$Sha256,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    if (Test-Path -LiteralPath $Destination) {
        $existingHash = (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($existingHash -ne $Sha256) {
            throw "Cached download has an unexpected SHA-256: $Destination"
        }
        return
    }

    $temporary = "$Destination.partial-$([Guid]::NewGuid().ToString('N'))"
    try {
        Invoke-WebRequest -Uri $Uri -OutFile $temporary -UseBasicParsing
        $actualHash = (Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actualHash -ne $Sha256) {
            throw "SHA-256 mismatch for $Uri. Expected $Sha256, got $actualHash."
        }
        Move-Item -LiteralPath $temporary -Destination $Destination
    }
    finally {
        if (Test-Path -LiteralPath $temporary) {
            Remove-Item -LiteralPath $temporary -Force
        }
    }
}

function Assert-ValidWindowsIcon {
    param(
        [Parameter(Mandatory = $true)][string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "The KeyWave application icon was not found: $Path"
    }

    $iconBytes = [System.IO.File]::ReadAllBytes($Path)
    if ($iconBytes.Length -lt 6) {
        throw "The KeyWave application icon has a truncated ICO header: $Path"
    }

    $reserved = [System.BitConverter]::ToUInt16($iconBytes, 0)
    $resourceType = [System.BitConverter]::ToUInt16($iconBytes, 2)
    $imageCount = [System.BitConverter]::ToUInt16($iconBytes, 4)
    if ($reserved -ne 0 -or $resourceType -ne 1 -or $imageCount -lt 1) {
        throw "The KeyWave application icon has an invalid ICO header: $Path"
    }

    $directorySize = 6 + (16 * [int]$imageCount)
    if ($iconBytes.Length -lt $directorySize) {
        throw "The KeyWave application icon has a truncated ICO directory: $Path"
    }

    $availableSizes = [System.Collections.Generic.HashSet[int]]::new()
    for ($index = 0; $index -lt $imageCount; $index++) {
        $entryOffset = 6 + (16 * $index)
        $width = if ($iconBytes[$entryOffset] -eq 0) { 256 } else { [int]$iconBytes[$entryOffset] }
        $height = if ($iconBytes[$entryOffset + 1] -eq 0) {
            256
        }
        else {
            [int]$iconBytes[$entryOffset + 1]
        }
        if ($width -ne $height) {
            throw "The KeyWave application icon contains a non-square ICO entry: $Path"
        }
        $null = $availableSizes.Add($width)
        $imageSize = [System.BitConverter]::ToUInt32($iconBytes, $entryOffset + 8)
        $imageOffset = [System.BitConverter]::ToUInt32($iconBytes, $entryOffset + 12)
        $imageEnd = [uint64]$imageOffset + [uint64]$imageSize
        if (
            $imageSize -eq 0 -or
            $imageOffset -lt $directorySize -or
            $imageEnd -gt [uint64]$iconBytes.Length
        ) {
            throw "The KeyWave application icon contains an invalid ICO image entry: $Path"
        }
    }

    foreach ($requiredSize in @(16, 20, 24, 32, 40, 48, 64, 128, 256)) {
        if (-not $availableSizes.Contains($requiredSize)) {
            throw "The KeyWave application icon is missing its $($requiredSize)x$requiredSize entry: $Path"
        }
    }
}

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Python 3.12 environment was not found: $Python"
}

$applicationIcon = Join-Path $repoRoot "build\branding\KeyWave.ico"
Assert-ValidWindowsIcon -Path $applicationIcon

$ytDlpCache = Join-Path $cacheRoot "yt-dlp-2026.08.19.exe"
Get-VerifiedFile `
    -Uri "https://github.com/yt-dlp/yt-dlp/releases/download/2026.08.19/yt-dlp.exe" `
    -Sha256 "66674953fe251b89f4d08c5f0e35e0728679bd67ab3d7d05c0562af101dd3e7a" `
    -Destination $ytDlpCache
$ytDlp = Join-Path $toolStage "yt-dlp.exe"
Copy-Item -LiteralPath $ytDlpCache -Destination $ytDlp -Force
$ytDlpLicenses = Join-Path $cacheRoot "YTDLP-2026.08.19-LICENSES.txt"
Get-VerifiedFile `
    -Uri "https://raw.githubusercontent.com/yt-dlp/yt-dlp/2026.08.19/THIRD_PARTY_LICENSES.txt" `
    -Sha256 "472aefe951c7db35e1657c1d13fd337140511ed6f2b329205105ad441c5a02b7" `
    -Destination $ytDlpLicenses

$denoArchive = Join-Path $cacheRoot "deno-2.8.1-x86_64-pc-windows-msvc.zip"
Get-VerifiedFile `
    -Uri "https://github.com/denoland/deno/releases/download/v2.8.1/deno-x86_64-pc-windows-msvc.zip" `
    -Sha256 "5fb5bac71f609fb91ec8960fb290885aadc27eeb22f07a8eca0c3db6be38b11a" `
    -Destination $denoArchive
if (-not (Test-Path -LiteralPath (Join-Path $toolStage "deno.exe"))) {
    Expand-Archive -LiteralPath $denoArchive -DestinationPath $toolStage
}

$ffmpegArchive = Join-Path $cacheRoot "ffmpeg-8.1.1-essentials_build.zip"
Get-VerifiedFile `
    -Uri "https://github.com/GyanD/codexffmpeg/releases/download/8.1.1/ffmpeg-8.1.1-essentials_build.zip" `
    -Sha256 "6f58ce889f59c311410f7d2b18895b33c03456463486f3b1ebc93d97a0f54541" `
    -Destination $ffmpegArchive
$ffmpegStage = Join-Path $cacheRoot "ffmpeg-8.1.1-essentials"
if (-not (Test-Path -LiteralPath (Join-Path $ffmpegStage "ffmpeg.exe"))) {
    $extraction = Join-Path $cacheRoot "ffmpeg-extract"
    if (Test-Path -LiteralPath $extraction) {
        $resolvedExtraction = [System.IO.Path]::GetFullPath($extraction)
        if (-not $resolvedExtraction.StartsWith($cacheRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to clean a path outside the download cache."
        }
        Remove-Item -LiteralPath $resolvedExtraction -Recurse -Force
    }
    New-Item -ItemType Directory -Force $extraction, $ffmpegStage | Out-Null
    Expand-Archive -LiteralPath $ffmpegArchive -DestinationPath $extraction
    $ffmpegExecutable = Get-ChildItem -LiteralPath $extraction -Recurse -Filter "ffmpeg.exe" |
        Select-Object -First 1
    $ffprobeExecutable = Get-ChildItem -LiteralPath $extraction -Recurse -Filter "ffprobe.exe" |
        Select-Object -First 1
    if ($null -eq $ffmpegExecutable -or $null -eq $ffprobeExecutable) {
        throw "The verified FFmpeg archive did not contain ffmpeg.exe and ffprobe.exe."
    }
    Copy-Item -LiteralPath $ffmpegExecutable.FullName -Destination (Join-Path $ffmpegStage "ffmpeg.exe")
    Copy-Item -LiteralPath $ffprobeExecutable.FullName -Destination (Join-Path $ffmpegStage "ffprobe.exe")
    $license = Get-ChildItem -LiteralPath $extraction -Recurse -Filter "LICENSE" | Select-Object -First 1
    if ($null -ne $license) {
        Copy-Item -LiteralPath $license.FullName -Destination (Join-Path $ffmpegStage "FFMPEG_LICENSE.txt")
    }
}

Push-Location $pythonRoot
try {
    & $Python -m pip install --disable-pip-version-check -e ".[build]"
    if ($LASTEXITCODE -ne 0) { throw "Build dependencies could not be installed." }
    & $Python -m nuitka `
        --mode=standalone `
        --enable-plugin=pyside6 `
        --include-qt-plugins=qml `
        --assume-yes-for-downloads `
        --python-flag=-m `
        --windows-console-mode=disable `
        --windows-icon-from-ico=$applicationIcon `
        --output-dir=$OutputRoot `
        --output-filename=KeyWaveCreator.exe `
        --include-package=keywave_creator `
        --include-package=scipy._external.array_api_compat.numpy `
        --include-package=scipy.signal `
        --include-data-dir=contract/schemas=keywave_creator/_schemas `
        --include-data-dir=src/keywave_creator/presentation/qml=keywave_creator/presentation/qml `
        --include-data-file=src/keywave_creator/presentation/assets/keywave-app-icon.png=keywave_creator/presentation/assets/keywave-app-icon.png `
        --remove-output `
        src/keywave_creator
    if ($LASTEXITCODE -ne 0) { throw "Nuitka Creator build failed." }
}
finally {
    Pop-Location
}

$distribution = Join-Path $OutputRoot "keywave_creator.dist"
if (-not (Test-Path -LiteralPath (Join-Path $distribution "KeyWaveCreator.exe"))) {
    throw "Nuitka did not produce the expected standalone distribution."
}
$distributionTools = Join-Path $distribution "tools"
New-Item -ItemType Directory -Force $distributionTools | Out-Null
Copy-Item -LiteralPath $ytDlp -Destination (Join-Path $distributionTools "yt-dlp.exe") -Force
Copy-Item -LiteralPath $ytDlpLicenses -Destination (Join-Path $distributionTools "YTDLP_LICENSES.txt") -Force
Copy-Item -LiteralPath (Join-Path $toolStage "deno.exe") -Destination (Join-Path $distributionTools "deno.exe") -Force
Copy-Item -LiteralPath (Join-Path $ffmpegStage "ffmpeg.exe") -Destination (Join-Path $distributionTools "ffmpeg.exe") -Force
Copy-Item -LiteralPath (Join-Path $ffmpegStage "ffprobe.exe") -Destination (Join-Path $distributionTools "ffprobe.exe") -Force
if (Test-Path -LiteralPath (Join-Path $ffmpegStage "FFMPEG_LICENSE.txt")) {
    Copy-Item -LiteralPath (Join-Path $ffmpegStage "FFMPEG_LICENSE.txt") `
        -Destination (Join-Path $distributionTools "FFMPEG_LICENSE.txt") -Force
}
Copy-Item -LiteralPath (Join-Path $repoRoot "THIRD_PARTY_NOTICES.md") -Destination $distribution -Force
Write-Output "KeyWave Creator built at $distribution"
