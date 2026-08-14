from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from keywave_creator.application.errors import CreatorError, CreatorErrorCode
from keywave_creator.application.models import CancellationToken, MediaInfo
from keywave_creator.infrastructure.media import FFmpegMediaProcessor
from keywave_creator.infrastructure.process import CommandResult


class FakeLocator:
    def resolve(self, executable_name: str, *, environment_variable: str | None = None) -> Path:
        del environment_variable
        return Path("C:/reviewed") / executable_name


class FakeRunner:
    def __init__(self, probe_payload: dict[str, object] | None = None) -> None:
        self.probe_payload = probe_payload
        self.commands: list[tuple[str, ...]] = []
        self.fail_cover = False

    def run(
        self,
        command: tuple[object, ...],
        *,
        timeout_seconds: float,
        cancellation: CancellationToken,
        working_directory: Path | None = None,
    ) -> CommandResult:
        del timeout_seconds, cancellation, working_directory
        arguments = tuple(str(item) for item in command)
        self.commands.append(arguments)
        if "-show_streams" in arguments:
            return CommandResult(json.dumps(self.probe_payload), "", 0.1)
        outputs = [
            Path(value)
            for value in arguments
            if Path(value).suffix.lower() in {".ogg", ".wav", ".webm", ".jpg"}
        ]
        for output in outputs:
            if self.fail_cover and output.suffix == ".jpg":
                raise CreatorError(CreatorErrorCode.PROCESS_FAILED, "cover failed")
            if output.suffix.lower() in {".ogg", ".wav"}:
                samples = np.zeros(48_000, dtype=np.float32)
                samples[4_800:4_900] = 0.8
                sf.write(output, samples, 48_000, format="WAV", subtype="PCM_16")
            else:
                output.write_bytes(b"normalized")
        return CommandResult("", "", 0.1)


def test_probe_reads_audio_video_duration_and_rate() -> None:
    runner = FakeRunner(
        {
            "format": {"duration": "5.25", "start_time": "0.100"},
            "streams": [
                {"codec_type": "audio", "duration": "5.0", "start_time": "0.125"},
                {
                    "codec_type": "video",
                    "width": 1920,
                    "height": 1080,
                    "avg_frame_rate": "30000/1001",
                    "start_time": "0.091",
                },
            ],
        }
    )
    processor = FFmpegMediaProcessor(runner=runner, locator=FakeLocator())  # type: ignore[arg-type]

    info = processor.probe(Path("source.mp4"), CancellationToken())

    assert info.duration_ms == 5250
    assert info.has_audio and info.has_video
    assert info.width == 1920 and info.height == 1080
    assert info.frames_per_second == pytest.approx(29.970, rel=0.001)
    assert info.audio_start_us == 125_000
    assert info.video_start_us == 91_000


def test_probe_rejects_invalid_metadata() -> None:
    runner = FakeRunner({"format": {}, "streams": "not-a-list"})
    processor = FFmpegMediaProcessor(runner=runner, locator=FakeLocator())  # type: ignore[arg-type]

    with pytest.raises(CreatorError) as raised:
        processor.probe(Path("source.mp4"), CancellationToken())

    assert raised.value.code is CreatorErrorCode.UNSUPPORTED_MEDIA


def test_normalize_builds_ogg_video_and_optional_cover(tmp_path: Path) -> None:
    runner = FakeRunner()
    processor = FFmpegMediaProcessor(runner=runner, locator=FakeLocator())  # type: ignore[arg-type]
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")

    normalized = processor.normalize(
        source,
        MediaInfo(duration_ms=12_000, has_audio=True, has_video=True),
        tmp_path,
        include_video=True,
        cancellation=CancellationToken(),
    )

    assert normalized.audio_path.is_file()
    assert normalized.analysis_path.is_file()
    assert normalized.timeline.sample_count == 48_000
    assert normalized.timeline.duration_ms == 1_000
    assert normalized.video_path is not None and normalized.video_path.is_file()
    assert normalized.video_path.suffix == ".webm"
    assert normalized.cover_path is not None and normalized.cover_path.is_file()
    assert any("libvorbis" in command for command in runner.commands)
    assert any("asplit=2" in " ".join(command) for command in runner.commands)
    assert any("libvpx" in command for command in runner.commands)
    assert not any("libx264" in command for command in runner.commands)


def test_cover_failure_does_not_discard_playable_audio(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.fail_cover = True
    processor = FFmpegMediaProcessor(runner=runner, locator=FakeLocator())  # type: ignore[arg-type]
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")

    normalized = processor.normalize(
        source,
        MediaInfo(duration_ms=2_000, has_audio=True, has_video=True),
        tmp_path,
        include_video=False,
        cancellation=CancellationToken(),
    )

    assert normalized.audio_path.is_file()
    assert normalized.analysis_path.is_file()
    assert normalized.video_path is None
    assert normalized.cover_path is None


def test_automatic_sync_measures_playback_codec_delay(tmp_path: Path) -> None:
    sample_rate = 48_000
    canonical = np.zeros(sample_rate * 6, dtype=np.float32)
    rng = np.random.default_rng(42)
    canonical[sample_rate : sample_rate * 5] = rng.normal(
        0,
        0.18,
        sample_rate * 4,
    )
    delay_samples = round(sample_rate * 0.037)
    playback = np.concatenate((np.zeros(delay_samples), canonical))[: canonical.size]
    canonical_path = tmp_path / "canonical.wav"
    playback_path = tmp_path / "playback.wav"
    sf.write(canonical_path, canonical, sample_rate, subtype="PCM_16")
    sf.write(playback_path, playback, sample_rate, subtype="PCM_16")

    report = FFmpegMediaProcessor._measure_sync(
        canonical_path,
        playback_path,
        CancellationToken(),
    )

    assert abs(report.analysis_to_playback_offset_us - 37_000) <= 1_000
    assert report.drift_us <= 2_000
    assert report.confidence >= 0.95
