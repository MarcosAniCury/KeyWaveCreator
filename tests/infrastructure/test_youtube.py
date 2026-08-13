from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path

import pytest

from keywave_creator.application.errors import CreatorError, CreatorErrorCode
from keywave_creator.application.models import CancellationToken, PlaylistTrack
from keywave_creator.infrastructure.process import CommandResult
from keywave_creator.infrastructure.youtube import (
    PRIMARY_VIDEO_FORMAT,
    PUBLIC_PROGRESSIVE_FALLBACK_FORMAT,
    YtDlpPublicVideoDownloader,
    YtDlpYouTubeMusicVideoFinder,
    YtDlpYouTubePlaylistResolver,
)


class RecordingRunner:
    def __init__(self) -> None:
        self.command: tuple[str, ...] = ()

    def run(
        self,
        command: Sequence[str | os.PathLike[str]],
        *,
        timeout_seconds: float,
        cancellation: CancellationToken,
        working_directory: Path | None = None,
    ) -> CommandResult:
        del timeout_seconds, cancellation
        assert working_directory is not None
        self.command = tuple(os.fspath(item) for item in command)
        (working_directory / "source.mkv").write_bytes(b"media")
        (working_directory / "source.info.json").write_text(
            json.dumps({"title": "Downloaded title", "uploader": "Video channel"}),
            encoding="utf-8",
        )
        return CommandResult(stdout="", stderr="", elapsed_seconds=0.1)


class FakeLocator:
    def resolve(self, executable_name: str, *, environment_variable: str | None = None) -> Path:
        del environment_variable
        return Path("C:/tools") / executable_name

    def optional(self, executable_name: str) -> Path | None:
        return Path("C:/tools") / executable_name


class JsonRunner:
    def __init__(self, payload: object) -> None:
        self._payload = payload
        self.command: tuple[str, ...] = ()

    def run(
        self,
        command: Sequence[str | os.PathLike[str]],
        *,
        timeout_seconds: float,
        cancellation: CancellationToken,
        working_directory: Path | None = None,
    ) -> CommandResult:
        del timeout_seconds, cancellation, working_directory
        self.command = tuple(os.fspath(item) for item in command)
        return CommandResult(stdout=json.dumps(self._payload), stderr="", elapsed_seconds=0.1)


class ForbiddenThenSuccessRunner:
    def __init__(self, *, fallback_forbidden: bool = False) -> None:
        self.commands: list[tuple[str, ...]] = []
        self._fallback_forbidden = fallback_forbidden

    def run(
        self,
        command: Sequence[str | os.PathLike[str]],
        *,
        timeout_seconds: float,
        cancellation: CancellationToken,
        working_directory: Path | None = None,
    ) -> CommandResult:
        del timeout_seconds, cancellation
        assert working_directory is not None
        normalized = tuple(os.fspath(item) for item in command)
        self.commands.append(normalized)
        format_selector = normalized[normalized.index("--format") + 1]
        if format_selector == PRIMARY_VIDEO_FORMAT or self._fallback_forbidden:
            (working_directory / "source.part").write_bytes(b"partial")
            raise CreatorError(
                CreatorErrorCode.PROCESS_FAILED,
                "yt-dlp.exe could not process the media.",
                diagnostic="ERROR: unable to download video data: HTTP Error 403: Forbidden",
            )
        (working_directory / "source.mp4").write_bytes(b"media")
        (working_directory / "source.info.json").write_text(
            json.dumps({"title": "Fallback title", "uploader": "Fallback channel"}),
            encoding="utf-8",
        )
        return CommandResult(stdout="", stderr="", elapsed_seconds=0.1)


@pytest.mark.parametrize(
    "url",
    (
        "http://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://example.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com/playlist?list=PL123",
        "https://www.youtube.com/watch?v=short",
        "https://user@www.youtube.com/watch?v=dQw4w9WgXcQ",
    ),
)
def test_restricted_downloader_rejects_non_single_public_urls(url: str) -> None:
    with pytest.raises(CreatorError) as raised:
        YtDlpPublicVideoDownloader._validate_url(url)
    assert raised.value.code is CreatorErrorCode.INVALID_SOURCE


def test_restricted_downloader_accepts_canonical_video_urls() -> None:
    assert (
        YtDlpPublicVideoDownloader._validate_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        == "dQw4w9WgXcQ"
    )
    assert YtDlpPublicVideoDownloader._validate_url("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert (
        YtDlpPublicVideoDownloader._validate_url(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDdQw4w9WgXcQ&start_radio=1"
        )
        == "dQw4w9WgXcQ"
    )
    assert (
        YtDlpPublicVideoDownloader._validate_url("https://www.youtube.com/shorts/dQw4w9WgXcQ")
        == "dQw4w9WgXcQ"
    )


def test_metadata_prefers_music_fields_and_normalizes_whitespace(tmp_path: Path) -> None:
    (tmp_path / "source.info.json").write_text(
        json.dumps(
            {
                "title": "Fallback title",
                "track": "  Better   Song  ",
                "uploader": "Fallback artist",
                "artist": "  KeyWave   Artist ",
            }
        ),
        encoding="utf-8",
    )

    assert YtDlpPublicVideoDownloader._read_metadata(tmp_path) == (
        "Better Song",
        "KeyWave Artist",
    )


def test_metadata_rejects_oversized_info_json(tmp_path: Path) -> None:
    (tmp_path / "source.info.json").write_bytes(b" " * (4 * 1024 * 1024 + 1))

    with pytest.raises(CreatorError) as raised:
        YtDlpPublicVideoDownloader._read_metadata(tmp_path)

    assert raised.value.code is CreatorErrorCode.PROCESS_FAILED


def test_download_strips_playlist_context_and_returns_metadata(tmp_path: Path) -> None:
    runner = RecordingRunner()
    downloader = YtDlpPublicVideoDownloader(runner=runner, locator=FakeLocator())

    result = downloader.download(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDdQw4w9WgXcQ&start_radio=1",
        tmp_path,
        CancellationToken(),
    )

    assert runner.command[-1] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert "--no-playlist" in runner.command
    assert result.title == "Downloaded title"
    assert result.artist == "Video channel"


def test_download_retries_http_403_with_public_progressive_format(tmp_path: Path) -> None:
    runner = ForbiddenThenSuccessRunner()
    downloader = YtDlpPublicVideoDownloader(runner=runner, locator=FakeLocator())

    result = downloader.download(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        tmp_path,
        CancellationToken(),
    )

    assert len(runner.commands) == 2
    assert runner.commands[0][runner.commands[0].index("--format") + 1] == PRIMARY_VIDEO_FORMAT
    assert (
        runner.commands[1][runner.commands[1].index("--format") + 1]
        == PUBLIC_PROGRESSIVE_FALLBACK_FORMAT
    )
    assert not (tmp_path / "source.part").exists()
    assert result.path == tmp_path / "source.mp4"
    assert result.title == "Fallback title"


def test_download_reports_actionable_message_when_public_formats_remain_forbidden(
    tmp_path: Path,
) -> None:
    downloader = YtDlpPublicVideoDownloader(
        runner=ForbiddenThenSuccessRunner(fallback_forbidden=True),
        locator=FakeLocator(),
    )

    with pytest.raises(CreatorError) as raised:
        downloader.download(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            tmp_path,
            CancellationToken(),
        )

    assert raised.value.code is CreatorErrorCode.PROCESS_FAILED
    assert raised.value.public_message == (
        "YouTube refused every public stream for this video. "
        "Try again later or choose another public video for the same song."
    )


def test_playlist_resolver_preserves_order_deduplicates_and_uses_canonical_urls() -> None:
    runner = JsonRunner(
        {
            "entries": [
                {"id": "video000001", "title": "First"},
                {"id": "video000001", "title": "Duplicate"},
                {"id": "video000002", "title": "Second"},
                {"id": None, "title": "Private"},
            ]
        }
    )
    resolver = YtDlpYouTubePlaylistResolver(runner=runner, locator=FakeLocator())

    videos = resolver.resolve(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL1234567890",
        CancellationToken(),
    )

    assert [video.label for video in videos] == ["First", "Second"]
    assert [video.url for video in videos] == [
        "https://www.youtube.com/watch?v=video000001",
        "https://www.youtube.com/watch?v=video000002",
    ]
    assert runner.command[-1] == "https://www.youtube.com/playlist?list=PL1234567890"
    assert "--flat-playlist" in runner.command


def test_music_video_finder_prefers_official_duration_match_and_rejects_cover() -> None:
    runner = JsonRunner(
        {
            "entries": [
                {
                    "id": "cover000001",
                    "title": "Artist Song acoustic cover",
                    "channel": "Cover channel",
                    "duration": 180,
                },
                {
                    "id": "official001",
                    "title": "Artist - Song (Official Music Video)",
                    "channel": "ArtistVEVO",
                    "duration": 181,
                },
            ]
        }
    )
    finder = YtDlpYouTubeMusicVideoFinder(runner=runner, locator=FakeLocator())

    video = finder.find(
        PlaylistTrack(title="Song", artist="Artist", duration_ms=180_000),
        CancellationToken(),
    )

    assert video is not None
    assert video.url == "https://www.youtube.com/watch?v=official001"
    assert runner.command[-1].startswith("ytsearch5:")
