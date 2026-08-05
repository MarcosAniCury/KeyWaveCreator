from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from keywave_creator.contract.errors import ContractError, ErrorCode
from keywave_creator.contract.models import ChartDescriptor, FileDescriptor, Manifest
from keywave_creator.contract.package import (
    PackageReader,
    PackageWriter,
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
