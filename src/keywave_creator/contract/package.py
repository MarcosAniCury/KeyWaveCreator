"""Safe deterministic `.keywave` package reader and writer."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import uuid
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import ContractError, ErrorCode, ValidationIssue
from .models import (
    Chart,
    ChartDescriptor,
    Difficulty,
    FileDescriptor,
    Manifest,
    validate_nested_charts,
)
from .schema import validate_wire_document

MAX_PACKAGE_BYTES = 2 * 1024 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 2304 * 1024 * 1024
MAX_ENTRY_COUNT = 32
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100
SAFE_PATH = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,127}$")


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _object_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(
                ValidationIssue(ErrorCode.INVALID_MANIFEST, f"Duplicate JSON key: {key}")
            )
        result[key] = value
    return result


def parse_json(data: bytes, *, member: str) -> dict[str, Any]:
    if len(data) > MAX_JSON_BYTES:
        raise ContractError(ValidationIssue(ErrorCode.LIMIT_EXCEEDED, "JSON is too large", member))
    try:
        text = data.decode("utf-8-sig")
        value = json.loads(text, object_pairs_hook=_object_no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(
            ValidationIssue(ErrorCode.INVALID_MANIFEST, f"Invalid JSON: {exc}", member)
        ) from exc
    if not isinstance(value, dict):
        raise ContractError(
            ValidationIssue(ErrorCode.INVALID_MANIFEST, "JSON root must be an object", member)
        )
    return value


def validate_member_path(name: str) -> None:
    pure = PurePosixPath(name)
    if (
        not SAFE_PATH.fullmatch(name)
        or "\\" in name
        or pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
        or ":" in name
    ):
        raise ContractError(ValidationIssue(ErrorCode.INVALID_PATH, "Unsafe ZIP member", name))


@dataclass(frozen=True, slots=True)
class PackageContents:
    manifest: Manifest
    charts: tuple[Chart, ...]
    package_sha256: str


class PackageReader:
    def read(self, path: Path) -> PackageContents:
        path = path.resolve()
        if not path.is_file() or path.stat().st_size > MAX_PACKAGE_BYTES:
            raise ContractError(
                ValidationIssue(ErrorCode.LIMIT_EXCEEDED, "Package is missing or too large")
            )
        package_hash = sha256_file(path)
        try:
            archive = zipfile.ZipFile(path, "r", allowZip64=False)
        except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
            raise ContractError(
                ValidationIssue(ErrorCode.INVALID_CONTAINER, "Invalid ZIP container")
            ) from exc
        with archive:
            infos = archive.infolist()
            self._preflight(infos)
            names = {info.filename for info in infos}
            if "manifest.json" not in names:
                raise ContractError(
                    ValidationIssue(ErrorCode.INVALID_MANIFEST, "manifest.json is missing")
                )
            manifest_value = parse_json(
                self._read_json_member(archive, "manifest.json"), member="manifest.json"
            )
            validate_wire_document(manifest_value, name="manifest", member="manifest.json")
            manifest = Manifest.from_dict(manifest_value)
            manifest.validate()
            descriptors: list[FileDescriptor] = [manifest.audio, *manifest.charts]
            if manifest.video:
                descriptors.append(manifest.video)
            if manifest.cover:
                descriptors.append(manifest.cover)
            expected_names = {"manifest.json", *(descriptor.path for descriptor in descriptors)}
            if names != expected_names:
                raise ContractError(
                    ValidationIssue(
                        ErrorCode.INVALID_CONTAINER, "Unexpected or missing ZIP members"
                    )
                )
            charts_by_difficulty: dict[Difficulty, Chart] = {}
            for descriptor in descriptors:
                info = archive.getinfo(descriptor.path)
                actual_hash = self._hash_member(archive, info)
                if info.file_size != descriptor.size_bytes or actual_hash != descriptor.sha256:
                    raise ContractError(
                        ValidationIssue(
                            ErrorCode.INTEGRITY_MISMATCH,
                            "Member size or SHA-256 does not match",
                            descriptor.path,
                        )
                    )
                if isinstance(descriptor, ChartDescriptor):
                    chart_value = parse_json(
                        self._read_json_member(archive, descriptor.path), member=descriptor.path
                    )
                    validate_wire_document(chart_value, name="chart", member=descriptor.path)
                    chart = Chart.from_dict(chart_value)
                    chart.validate(duration_ms=manifest.duration_ms)
                    if (
                        chart.chart_id != descriptor.chart_id
                        or chart.difficulty != descriptor.difficulty
                    ):
                        raise ContractError(
                            ValidationIssue(ErrorCode.INVALID_CHART, "Chart descriptor mismatch")
                        )
                    if len(chart.notes) != descriptor.note_count:
                        raise ContractError(
                            ValidationIssue(ErrorCode.INVALID_CHART, "Chart note count mismatch")
                        )
                    charts_by_difficulty[chart.difficulty] = chart
            charts = tuple(charts_by_difficulty[difficulty] for difficulty in Difficulty)
            validate_nested_charts(charts)
            return PackageContents(manifest=manifest, charts=charts, package_sha256=package_hash)

    @staticmethod
    def _preflight(infos: list[zipfile.ZipInfo]) -> None:
        if not infos or len(infos) > MAX_ENTRY_COUNT:
            raise ContractError(
                ValidationIssue(ErrorCode.LIMIT_EXCEEDED, "Invalid ZIP entry count")
            )
        seen: set[str] = set()
        total = 0
        for info in infos:
            validate_member_path(info.filename)
            folded = info.filename.casefold()
            unix_mode = (info.external_attr >> 16) & 0xFFFF
            if folded in seen or info.is_dir() or stat.S_ISLNK(unix_mode):
                raise ContractError(
                    ValidationIssue(
                        ErrorCode.INVALID_CONTAINER, "Duplicate or directory ZIP member"
                    )
                )
            seen.add(folded)
            if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                raise ContractError(
                    ValidationIssue(ErrorCode.INVALID_CONTAINER, "Unsupported compression method")
                )
            total += info.file_size
            if total > MAX_UNCOMPRESSED_BYTES:
                raise ContractError(
                    ValidationIssue(ErrorCode.LIMIT_EXCEEDED, "Package expands beyond its quota")
                )
            if info.file_size > 0 and info.compress_size == 0:
                raise ContractError(
                    ValidationIssue(ErrorCode.LIMIT_EXCEEDED, "Invalid compressed member size")
                )
            if info.compress_size and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
                raise ContractError(
                    ValidationIssue(ErrorCode.LIMIT_EXCEEDED, "Compression ratio is unsafe")
                )

    @staticmethod
    def _read_json_member(archive: zipfile.ZipFile, name: str) -> bytes:
        info = archive.getinfo(name)
        if info.file_size > MAX_JSON_BYTES:
            raise ContractError(
                ValidationIssue(ErrorCode.LIMIT_EXCEEDED, "JSON is too large", member=name)
            )
        with archive.open(info, "r") as stream:
            return stream.read(MAX_JSON_BYTES + 1)

    @staticmethod
    def _hash_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
        digest = hashlib.sha256()
        with archive.open(info, "r") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()


class PackageWriter:
    def write(
        self,
        *,
        destination: Path,
        manifest: Manifest,
        charts: tuple[Chart, ...],
        audio_path: Path,
        video_path: Path | None = None,
        cover_path: Path | None = None,
    ) -> Path:
        manifest.validate()
        for chart in charts:
            chart.validate(duration_ms=manifest.duration_ms)
        validate_nested_charts(charts)
        source_by_member: dict[str, Path] = {manifest.audio.path: audio_path}
        if manifest.video and video_path:
            source_by_member[manifest.video.path] = video_path
        if manifest.cover and cover_path:
            source_by_member[manifest.cover.path] = cover_path
        chart_bytes = {
            descriptor.path: canonical_json(chart.to_dict())
            for descriptor, chart in zip(manifest.charts, charts, strict=True)
        }
        self._validate_descriptors(manifest, source_by_member, chart_bytes)
        destination = destination.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + f".partial-{uuid.uuid4().hex}")
        try:
            with zipfile.ZipFile(partial, "w", allowZip64=False) as archive:
                self._write_bytes(
                    archive, "manifest.json", canonical_json(manifest.to_dict()), True
                )
                for name in sorted(chart_bytes):
                    self._write_bytes(archive, name, chart_bytes[name], True)
                for name in sorted(source_by_member):
                    self._write_file(archive, name, source_by_member[name])
            PackageReader().read(partial)
            os.replace(partial, destination)
        finally:
            partial.unlink(missing_ok=True)
        return destination

    @staticmethod
    def _validate_descriptors(
        manifest: Manifest,
        source_by_member: dict[str, Path],
        chart_bytes: dict[str, bytes],
    ) -> None:
        descriptors: Iterable[FileDescriptor] = (
            manifest.audio,
            *manifest.charts,
            *((manifest.video,) if manifest.video else ()),
            *((manifest.cover,) if manifest.cover else ()),
        )
        for descriptor in descriptors:
            validate_member_path(descriptor.path)
            if descriptor.path in chart_bytes:
                data = chart_bytes[descriptor.path]
                actual_size, actual_hash = len(data), sha256_bytes(data)
            else:
                path = source_by_member.get(descriptor.path)
                if path is None or not path.is_file():
                    raise ContractError(
                        ValidationIssue(
                            ErrorCode.INVALID_INPUT,
                            f"Missing source for {descriptor.path}",
                        )
                    )
                actual_size, actual_hash = path.stat().st_size, sha256_file(path)
            if actual_size != descriptor.size_bytes or actual_hash != descriptor.sha256:
                raise ContractError(ValidationIssue(ErrorCode.INTEGRITY_MISMATCH, descriptor.path))

    @staticmethod
    def _write_bytes(archive: zipfile.ZipFile, name: str, data: bytes, compress: bool) -> None:
        info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
        info.external_attr = 0o100644 << 16
        archive.writestr(info, data)

    @classmethod
    def _write_file(cls, archive: zipfile.ZipFile, name: str, path: Path) -> None:
        info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_STORED
        info.external_attr = 0o100644 << 16
        info.file_size = path.stat().st_size
        with path.open("rb") as source, archive.open(info, "w", force_zip64=False) as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)


def assemble_package(
    *,
    destination: Path,
    title: str,
    artist: str,
    duration_ms: int,
    charts: tuple[Chart, ...],
    audio_path: Path,
    video_path: Path | None,
    cover_path: Path | None,
    generator_version: str,
    algorithm_version: str,
    seed: str,
    source_type: str = "local",
    source_id: str | None = None,
    generation_confidence: float = 1.0,
    chart_offset_ms: int = 0,
    video_offset_ms: int = 0,
) -> PackageContents:
    """Build descriptors, atomically write a package, then read it back as proof."""
    audio_hash = sha256_file(audio_path)
    chart_descriptors: list[ChartDescriptor] = []
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
    video = (
        FileDescriptor(
            path="video/background.mp4",
            size_bytes=video_path.stat().st_size,
            sha256=sha256_file(video_path),
            media_type="video/mp4",
        )
        if video_path
        else None
    )
    cover = (
        FileDescriptor(
            path="cover/cover.jpg",
            size_bytes=cover_path.stat().st_size,
            sha256=sha256_file(cover_path),
            media_type="image/jpeg",
        )
        if cover_path
        else None
    )
    identity = canonical_json(
        {
            "algorithmVersion": algorithm_version,
            "artist": artist,
            "audioSha256": audio_hash,
            "chartOffsetMs": chart_offset_ms,
            "chartSha256": [descriptor.sha256 for descriptor in chart_descriptors],
            "confidence": generation_confidence,
            "coverSha256": cover.sha256 if cover else None,
            "durationMs": duration_ms,
            "seed": seed,
            "sourceId": source_id,
            "sourceType": source_type,
            "title": title,
            "videoOffsetMs": video_offset_ms,
            "videoSha256": video.sha256 if video else None,
        }
    )
    package_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"keywave:{sha256_bytes(identity)}"))
    manifest = Manifest(
        package_id=package_id,
        title=title,
        artist=artist,
        duration_ms=duration_ms,
        generator_version=generator_version,
        algorithm_version=algorithm_version,
        seed=seed,
        generation_confidence=generation_confidence,
        source_type=source_type,
        source_id=source_id,
        chart_offset_ms=chart_offset_ms,
        video_offset_ms=video_offset_ms,
        audio=FileDescriptor(
            path="audio/song.ogg",
            size_bytes=audio_path.stat().st_size,
            sha256=audio_hash,
            media_type="audio/ogg",
        ),
        video=video,
        cover=cover,
        charts=tuple(chart_descriptors),
    )
    PackageWriter().write(
        destination=destination,
        manifest=manifest,
        charts=charts,
        audio_path=audio_path,
        video_path=video_path,
        cover_path=cover_path,
    )
    return PackageReader().read(destination)
