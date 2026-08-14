from __future__ import annotations

from pathlib import Path

from keywave_creator.application.models import (
    AcquiredMedia,
    CancellationToken,
    CreateLevelRequest,
    MediaInfo,
    NormalizedMedia,
    SourceKind,
)
from keywave_creator.application.service import CreatorService
from keywave_creator.contract.package import PackageReader
from keywave_creator.domain.analysis import AnalysisResult, FeaturePoint
from keywave_creator.domain.media_timeline import AutomaticSyncReport, CanonicalAudioTimeline


class FakeMediaProcessor:
    def probe(self, path: Path, cancellation: CancellationToken) -> MediaInfo:
        cancellation.raise_if_cancelled()
        assert path.is_file()
        return MediaInfo(duration_ms=5_000, has_audio=True, has_video=False)

    def normalize(
        self,
        source: Path,
        info: MediaInfo,
        working_directory: Path,
        *,
        include_video: bool,
        cancellation: CancellationToken,
    ) -> NormalizedMedia:
        cancellation.raise_if_cancelled()
        audio = working_directory / "song.ogg"
        audio.write_bytes(b"OggS\x00application-test")
        analysis = working_directory / "analysis.wav"
        analysis.write_bytes(b"RIFFapplication-test")
        return NormalizedMedia(
            audio_path=audio,
            analysis_path=analysis,
            video_path=None,
            cover_path=None,
            timeline=CanonicalAudioTimeline(
                sample_rate_hz=48_000,
                sample_count=240_000,
                source_audio_start_us=100_000,
                source_video_start_us=66_000,
                sync_report=AutomaticSyncReport(12_000, 1_000, 0.98),
            ),
        )


class FakeExtractor:
    def analyze(
        self,
        audio_path: Path,
        *,
        expected_duration_ms: int,
        cancellation: CancellationToken,
    ) -> AnalysisResult:
        cancellation.raise_if_cancelled()
        return AnalysisResult(
            duration_ms=expected_duration_ms,
            bpm=120.0,
            confidence=0.8,
            points=tuple(
                FeaturePoint(
                    time_ms=500 + index * 400,
                    strength=float(index % 4 + 1),
                    low_frequency_bias=0.5,
                    sustain_ms=500 if index == 3 else 0,
                    is_beat=True,
                )
                for index in range(10)
            ),
        )


class FailingDownloader:
    def download(
        self,
        url: str,
        working_directory: Path,
        cancellation: CancellationToken,
    ) -> AcquiredMedia:
        raise AssertionError("Local creation must not call the downloader")


class MetadataDownloader:
    def download(
        self,
        url: str,
        working_directory: Path,
        cancellation: CancellationToken,
    ) -> AcquiredMedia:
        cancellation.raise_if_cancelled()
        assert url.startswith("https://")
        source = working_directory / "source.webm"
        source.write_bytes(b"synthetic")
        return AcquiredMedia(
            path=source,
            source_id="dQw4w9WgXcQ",
            title="A Song: With / Invalid? Characters",
            artist="Automatic Artist",
        )


def test_service_creates_a_valid_package_and_reports_all_stages(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    source.write_bytes(b"synthetic")
    destination = tmp_path / "result.keywave"
    service = CreatorService(
        media_processor=FakeMediaProcessor(),
        feature_extractor=FakeExtractor(),
        video_downloader=FailingDownloader(),
    )
    stages: list[str] = []

    result = service.create(
        CreateLevelRequest(
            source_kind=SourceKind.LOCAL,
            source=str(source),
            title="Application Test",
            artist="KeyWave",
            destination=destination,
            seed="application-test",
        ),
        progress=lambda update: stages.append(update.stage.value),
    )

    package = PackageReader().read(destination)
    assert result.package_id == package.manifest.package_id
    assert result.note_counts == tuple(len(chart.notes) for chart in package.charts)
    assert package.manifest.chart_offset_ms == 12
    assert package.manifest.video_offset_ms == -34
    assert stages == [
        "validating",
        "acquiring",
        "normalizing",
        "analyzing",
        "generating",
        "packaging",
        "complete",
    ]


def test_youtube_creation_uses_acquired_metadata_and_available_filename(tmp_path: Path) -> None:
    output_directory = tmp_path / "levels"
    output_directory.mkdir()
    existing = output_directory / "Automatic Artist - A Song- With - Invalid- Characters.keywave"
    existing.write_bytes(b"existing-user-file")
    service = CreatorService(
        media_processor=FakeMediaProcessor(),
        feature_extractor=FakeExtractor(),
        video_downloader=MetadataDownloader(),
    )

    result = service.create(
        CreateLevelRequest(
            source_kind=SourceKind.YOUTUBE,
            source="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            output_directory=output_directory,
        )
    )

    assert result.title == "A Song: With / Invalid? Characters"
    assert result.artist == "Automatic Artist"
    assert (
        result.destination.name
        == "Automatic Artist - A Song- With - Invalid- Characters (2).keywave"
    )
    assert existing.read_bytes() == b"existing-user-file"
    package = PackageReader().read(result.destination)
    assert package.manifest.title == result.title
    assert package.manifest.artist == result.artist
