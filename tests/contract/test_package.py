from __future__ import annotations

import json
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from keywave_creator.contract.errors import ContractError, ErrorCode
from keywave_creator.contract.models import ChartDescriptor, FileDescriptor, Manifest
from keywave_creator.contract.package import (
    PackageReader,
    PackageWriter,
    assemble_package,
    canonical_json,
    sha256_bytes,
)

from .test_models import make_charts


def build_manifest(audio: bytes) -> tuple[Manifest, tuple]:
    charts = make_charts()
    chart_descriptors = []
    for chart in charts:
        payload = canonical_json(chart.to_dict())
        chart_descriptors.append(
            ChartDescriptor(
                path=f"charts/{chart.difficulty.value}.json",
                size_bytes=len(payload),
                sha256=sha256_bytes(payload),
                media_type="application/vnd.keywave.chart+json",
                difficulty=chart.difficulty,
                chart_id=chart.chart_id,
                note_count=len(chart.notes),
                active_lanes=chart.active_lanes,
            )
        )
    return (
        Manifest(
            package_id="package-test",
            title="Synthetic Song",
            artist="KeyWave Tests",
            duration_ms=5_000,
            generator_version="0.1.0",
            algorithm_version="1.0.0",
            seed="12345",
            audio=FileDescriptor(
                path="audio/song.ogg",
                size_bytes=len(audio),
                sha256=sha256_bytes(audio),
                media_type="audio/ogg",
            ),
            charts=tuple(chart_descriptors),
        ),
        charts,
    )


def test_package_round_trip_is_deterministic(tmp_path: Path) -> None:
    audio = b"OggS\x00synthetic-audio"
    audio_path = tmp_path / "song.ogg"
    audio_path.write_bytes(audio)
    manifest, charts = build_manifest(audio)
    first = tmp_path / "first.keywave"
    second = tmp_path / "second.keywave"
    writer = PackageWriter()
    writer.write(destination=first, manifest=manifest, charts=charts, audio_path=audio_path)
    writer.write(destination=second, manifest=manifest, charts=charts, audio_path=audio_path)

    assert first.read_bytes() == second.read_bytes()
    package = PackageReader().read(first)
    assert package.manifest == manifest
    assert package.charts == charts


def test_legacy_mp4_package_remains_readable(tmp_path: Path) -> None:
    audio = b"OggS\x00synthetic-audio"
    video = b"synthetic-legacy-mp4"
    audio_path = tmp_path / "song.ogg"
    video_path = tmp_path / "background.mp4"
    audio_path.write_bytes(audio)
    video_path.write_bytes(video)
    manifest, charts = build_manifest(audio)
    manifest = replace(
        manifest,
        video=FileDescriptor(
            path="video/background.mp4",
            size_bytes=len(video),
            sha256=sha256_bytes(video),
            media_type="video/mp4",
        ),
    )
    package_path = tmp_path / "legacy.keywave"

    PackageWriter().write(
        destination=package_path,
        manifest=manifest,
        charts=charts,
        audio_path=audio_path,
        video_path=video_path,
    )

    package = PackageReader().read(package_path)
    assert package.manifest.video == manifest.video


def test_package_rejects_unexpected_member(tmp_path: Path) -> None:
    package = tmp_path / "unsafe.keywave"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"not": "valid"}))
        archive.writestr("../escape.exe", b"bad")
    with pytest.raises(ContractError) as raised:
        PackageReader().read(package)
    assert raised.value.issue.code is ErrorCode.INVALID_PATH


def test_package_rejects_duplicate_casefolded_member(tmp_path: Path) -> None:
    package = tmp_path / "duplicate.keywave"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("manifest.json", b"{}")
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("manifest.json", b"{}")
    with pytest.raises(ContractError) as raised:
        PackageReader().read(package)
    assert raised.value.issue.code is ErrorCode.INVALID_CONTAINER


def test_assemble_package_feature_gates_vp8_webm_video(tmp_path: Path) -> None:
    audio_path = tmp_path / "song.ogg"
    video_path = tmp_path / "background.webm"
    destination = tmp_path / "vp8.keywave"
    audio_path.write_bytes(b"OggS\x00synthetic-audio")
    video_path.write_bytes(b"\x1aE\xdf\xa3synthetic-webm")

    package = assemble_package(
        destination=destination,
        title="Synthetic Song",
        artist="KeyWave Tests",
        duration_ms=5_000,
        charts=make_charts(),
        audio_path=audio_path,
        video_path=video_path,
        cover_path=None,
        generator_version="0.1.0",
        algorithm_version="1.0.0",
        seed="12345",
    )

    assert package.manifest.video is not None
    assert package.manifest.video.path == "video/background.webm"
    assert package.manifest.video.media_type == "video/webm"
    assert package.manifest.required_features == ("holdNotes", "vp8Video")
    assert destination.is_file()


def test_fixture_rejects_webm_without_vp8_feature() -> None:
    fixture = (
        Path(__file__).parents[2]
        / "contract"
        / "fixtures"
        / "1.0"
        / "invalid"
        / "webm-without-vp8-feature.keywave"
    )

    with pytest.raises(ContractError) as raised:
        PackageReader().read(fixture)

    assert raised.value.issue.code is ErrorCode.INVALID_MANIFEST
