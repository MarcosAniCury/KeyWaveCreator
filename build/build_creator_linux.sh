#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_root="$repo_root"
version="$(tr -d '\r\n' < "$repo_root/VERSION")"
output_root="${KEYWAVE_OUTPUT_ROOT:-$repo_root/artifacts/linux}"
cache_root="${KEYWAVE_DOWNLOAD_CACHE:-$repo_root/artifacts/download-cache}"
build_root="${KEYWAVE_LINUX_BUILD_ROOT:-$repo_root/artifacts/creator-linux-build}"
venv="$build_root/venv"
nuitka_output="$build_root/nuitka"
tool_stage="$build_root/tools"
distribution_name="KeyWave-Creator-$version-linux-x64"
distribution="$output_root/$distribution_name"
package_root="$build_root/package"
staged_distribution="$package_root/$distribution_name"
archive="$output_root/$distribution_name.tar.gz"

ffmpeg_name="ffmpeg-n8.1.2-34-g9b6c8969e0-linux64-gpl-8.1.tar.xz"
ffmpeg_url="https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-08-07-13-13/$ffmpeg_name"
ffmpeg_sha256="90390739c25ba52e37ac3c0dcda66f973996a4f91e656bb1d57cea18d7dc7c5d"
yt_dlp_name="yt-dlp-2026.07.04-linux"
yt_dlp_url="https://github.com/yt-dlp/yt-dlp/releases/download/2026.07.04/yt-dlp"
yt_dlp_sha256="495be29ff4d9d4e9be7eabdfef225221e5d5282e77f2f505abc6dca80349f3fd"
deno_name="deno-2.8.1-x86_64-unknown-linux-gnu.zip"
deno_url="https://github.com/denoland/deno/releases/download/v2.8.1/deno-x86_64-unknown-linux-gnu.zip"
deno_sha256="2d7bb6195226ac832e0bf7109a115f0af65ee69ac797a4bbde5b27a06cc242d9"

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        printf 'Required Linux build command was not found: %s\n' "$1" >&2
        exit 1
    fi
}

download_verified() {
    local url="$1"
    local expected_sha256="$2"
    local destination="$3"
    local actual_sha256

    if [[ -f "$destination" ]]; then
        actual_sha256="$(sha256sum "$destination" | cut -d ' ' -f 1)"
        if [[ "$actual_sha256" == "$expected_sha256" ]]; then
            return
        fi
        rm -f -- "$destination"
    fi

    local temporary="$destination.partial-$$"
    trap 'rm -f -- "$temporary"' RETURN
    curl --fail --location --retry 3 --proto '=https' --tlsv1.2 \
        --output "$temporary" "$url"
    actual_sha256="$(sha256sum "$temporary" | cut -d ' ' -f 1)"
    if [[ "$actual_sha256" != "$expected_sha256" ]]; then
        printf 'SHA-256 mismatch for %s\n' "$url" >&2
        exit 1
    fi
    mv -- "$temporary" "$destination"
    trap - RETURN
}

require_command curl
require_command gcc
require_command patchelf
require_command python3.12
require_command sha256sum
require_command tar

mkdir -p "$output_root" "$cache_root" "$build_root" "$tool_stage"

ffmpeg_archive="$cache_root/$ffmpeg_name"
yt_dlp="$cache_root/$yt_dlp_name"
deno_archive="$cache_root/$deno_name"
download_verified "$ffmpeg_url" "$ffmpeg_sha256" "$ffmpeg_archive"
download_verified "$yt_dlp_url" "$yt_dlp_sha256" "$yt_dlp"
download_verified "$deno_url" "$deno_sha256" "$deno_archive"

rm -rf -- "$tool_stage/ffmpeg" "$tool_stage/deno"
mkdir -p "$tool_stage/ffmpeg" "$tool_stage/deno"
tar -xJf "$ffmpeg_archive" --strip-components=1 -C "$tool_stage/ffmpeg"
python3.12 -m zipfile -e "$deno_archive" "$tool_stage/deno"

if [[ ! -x "$venv/bin/python" ]] || ! "$venv/bin/python" -m pip --version >/dev/null 2>&1; then
    rm -rf -- "$venv"
    python3.12 -m venv "$venv"
fi
"$venv/bin/python" -m pip install --disable-pip-version-check --upgrade pip
"$venv/bin/python" -m pip install --disable-pip-version-check "${python_root}[build]"

compiled_distribution="$nuitka_output/keywave_creator.dist"
if [[ "${KEYWAVE_REUSE_NUITKA_OUTPUT:-false}" != "true" ]] || \
    [[ ! -x "$compiled_distribution/KeyWaveCreator" ]]; then
    rm -rf -- "$nuitka_output"
    mkdir -p "$nuitka_output"
    pushd "$python_root" >/dev/null
    "$venv/bin/python" -m nuitka \
        --mode=standalone \
        --enable-plugin=pyside6 \
        --include-qt-plugins=qml \
        --noinclude-dlls='**/*.o' \
        --assume-yes-for-downloads \
        --python-flag=-m \
        --output-dir="$nuitka_output" \
        --output-filename=KeyWaveCreator \
        --include-package=keywave_creator \
        --include-package=rpds \
        --include-package-data=jsonschema_specifications \
        --include-package=scipy._external.array_api_compat.numpy \
        --include-package=scipy.signal \
        --include-data-dir=contract/schemas=keywave_creator/_schemas \
        --include-data-dir=src/keywave_creator/presentation/qml=keywave_creator/presentation/qml \
        --include-data-file=src/keywave_creator/presentation/assets/keywave-app-icon.png=keywave_creator/presentation/assets/keywave-app-icon.png \
        --remove-output \
        src/keywave_creator
    popd >/dev/null
fi

if [[ ! -x "$compiled_distribution/KeyWaveCreator" ]]; then
    printf 'Nuitka did not produce the expected Linux executable.\n' >&2
    exit 1
fi

rm -rf -- "$package_root"
mkdir -p "$package_root"
cp -a -- "$compiled_distribution" "$staged_distribution"
mkdir -p "$staged_distribution/tools"
install -m 0755 "$yt_dlp" "$staged_distribution/tools/yt-dlp"
install -m 0755 "$tool_stage/deno/deno" "$staged_distribution/tools/deno"
install -m 0755 "$tool_stage/ffmpeg/bin/ffmpeg" "$staged_distribution/tools/ffmpeg"
install -m 0755 "$tool_stage/ffmpeg/bin/ffprobe" "$staged_distribution/tools/ffprobe"
install -m 0644 \
    "$repo_root/THIRD_PARTY_NOTICES.md" \
    "$staged_distribution/THIRD_PARTY_NOTICES.md"
if [[ -f "$tool_stage/ffmpeg/LICENSE.txt" ]]; then
    install -m 0644 \
        "$tool_stage/ffmpeg/LICENSE.txt" \
        "$staged_distribution/tools/FFMPEG_LICENSE.txt"
fi

rm -f -- "$archive"
tar --sort=name --owner=0 --group=0 --numeric-owner --mtime='UTC 1970-01-01' \
    -czf "$archive" -C "$package_root" "$distribution_name"

"$staged_distribution/KeyWaveCreator" self-test
rm -rf -- "$distribution"
cp -a --no-preserve=ownership -- "$staged_distribution" "$distribution"
printf 'KeyWave Creator Linux build: %s\n' "$distribution"
printf 'KeyWave Creator Linux archive: %s\n' "$archive"
