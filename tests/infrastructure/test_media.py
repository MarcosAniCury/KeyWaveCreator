from __future__ import annotations

import json
from pathlib import Path

import pytest

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
        output = Path(arguments[-1])
        if self.fail_cover and output.suffix == ".jpg":
            raise CreatorError(CreatorErrorCode.PROCESS_FAILED, "cover failed")
        output.write_bytes(b"normalized")
        return CommandResult("", "", 0.1)


def test_probe_reads_audio_video_duration_and_rate() -> None:
    runner = FakeRunner(
        {
            "format": {"duration": "5.25"},
            "streams": [
                {"codec_type": "audio", "duration": "5.0"},
                {
                    "codec_type": "video",
                    "width": 1920,
                    "height": 1080,
                    "avg_frame_rate": "30000/1001",
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

    assert normalized.audio_path.read_bytes() == b"normalized"
    assert normalized.video_path is not None and normalized.video_path.is_file()
    assert normalized.video_path.suffix == ".webm"
    assert normalized.cover_path is not None and normalized.cover_path.is_file()
    assert any("libvorbis" in command for command in runner.commands)
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
    assert normalized.video_path is None
    assert normalized.cover_path is None
