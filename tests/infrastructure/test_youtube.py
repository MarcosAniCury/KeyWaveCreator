from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path

import pytest

from keywave_creator.application.errors import CreatorError, CreatorErrorCode
from keywave_creator.application.models import CancellationToken
from keywave_creator.infrastructure.process import CommandResult
from keywave_creator.infrastructure.youtube import YtDlpPublicVideoDownloader


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
