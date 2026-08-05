"""Restricted public single-video acquisition through pinned yt-dlp."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from keywave_creator.application.errors import CreatorError, CreatorErrorCode
from keywave_creator.application.models import AcquiredMedia, CancellationToken

from .process import ProcessRunner, ToolLocator

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
MAX_URL_LENGTH = 2_048
MAX_INFO_JSON_BYTES = 4 * 1024 * 1024
MAX_METADATA_LENGTH = 200
ALLOWED_HOSTS = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtu.be",
        "www.youtu.be",
    }
)


class YtDlpPublicVideoDownloader:
    """Download one public YouTube video without credentials or access bypasses."""

    def __init__(
        self,
        *,
        runner: ProcessRunner | None = None,
        locator: ToolLocator | None = None,
    ) -> None:
        self._runner = runner or ProcessRunner()
        self._locator = locator or ToolLocator()

    def download(
        self,
        url: str,
        working_directory: Path,
        cancellation: CancellationToken,
    ) -> AcquiredMedia:
        video_id = self._validate_url(url)
        canonical_url = f"https://www.youtube.com/watch?v={video_id}"
        command = [*self._command_prefix(), "--no-config", "--no-update", "--no-playlist"]
        ffmpeg_name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
        ffmpeg = self._locator.resolve(ffmpeg_name, environment_variable="KEYWAVE_FFMPEG")
        command.extend(
            (
                "--no-cache-dir",
                "--no-warnings",
                "--write-info-json",
                "--clean-info-json",
                "--socket-timeout",
                "30",
                "--retries",
                "3",
                "--fragment-retries",
                "3",
                "--max-filesize",
                "2G",
                "--merge-output-format",
                "mkv",
                "--ffmpeg-location",
                str(ffmpeg.parent),
                "--format",
                "bestvideo[height<=1080][fps<=30]+bestaudio/best[height<=1080][fps<=30]",
                "--output",
                str(working_directory / "source.%(ext)s"),
            )
        )
        if deno := self._locator.optional("deno.exe" if sys.platform == "win32" else "deno"):
            command.extend(("--js-runtimes", f"deno:{deno}"))
        command.append(canonical_url)
        self._runner.run(
            command,
            timeout_seconds=60 * 60,
            cancellation=cancellation,
            working_directory=working_directory,
        )
        candidates = sorted(
            path
            for path in working_directory.glob("source.*")
            if path.is_file() and path.suffix.casefold() not in {".part", ".ytdl", ".json"}
        )
        if len(candidates) != 1:
            raise CreatorError(
                CreatorErrorCode.PROCESS_FAILED,
                "The public video download did not produce one media file.",
            )
        title, artist = self._read_metadata(working_directory)
        return AcquiredMedia(
            path=candidates[0],
            source_id=video_id,
            title=title,
            artist=artist,
        )

    def _command_prefix(self) -> tuple[Path | str, ...]:
        executable_name = "yt-dlp.exe" if sys.platform == "win32" else "yt-dlp"
        if executable := self._locator.optional(executable_name):
            return (executable,)
        if importlib.util.find_spec("yt_dlp") is not None:
            return (Path(sys.executable).resolve(), "-m", "yt_dlp")
        raise CreatorError(
            CreatorErrorCode.TOOL_NOT_FOUND,
            "The pinned yt-dlp release is not installed.",
        )

    @staticmethod
    def _validate_url(url: str) -> str:
        value = url.strip()
        if len(value) > MAX_URL_LENGTH:
            raise CreatorError(
                CreatorErrorCode.INVALID_SOURCE,
                "The YouTube URL is too long.",
            )
        parsed = urlparse(value)
        host = (parsed.hostname or "").casefold()
        try:
            port = parsed.port
        except ValueError:
            port = -1
        if (
            parsed.scheme != "https"
            or host not in ALLOWED_HOSTS
            or parsed.username is not None
            or parsed.password is not None
            or port not in {None, 443}
        ):
            raise CreatorError(
                CreatorErrorCode.INVALID_SOURCE,
                "Enter an HTTPS URL for one public YouTube video.",
            )
        query = parse_qs(parsed.query)
        if host.endswith("youtu.be"):
            video_id = parsed.path.strip("/").split("/", 1)[0]
        else:
            values = query.get("v", [])
            video_id = (
                values[0] if values else YtDlpPublicVideoDownloader._path_video_id(parsed.path)
            )
        if not VIDEO_ID.fullmatch(video_id):
            raise CreatorError(
                CreatorErrorCode.INVALID_SOURCE,
                "The YouTube video URL is not valid.",
            )
        return video_id

    @staticmethod
    def _path_video_id(path: str) -> str:
        parts = [part for part in path.split("/") if part]
        if len(parts) == 2 and parts[0].casefold() in {"embed", "live", "shorts"}:
            return parts[1]
        return ""

    @staticmethod
    def _read_metadata(working_directory: Path) -> tuple[str | None, str | None]:
        candidates = sorted(working_directory.glob("source*.info.json"))
        if len(candidates) != 1:
            raise CreatorError(
                CreatorErrorCode.PROCESS_FAILED,
                "The video details could not be read. Try the URL again.",
            )
        metadata_path = candidates[0]
        try:
            if metadata_path.stat().st_size > MAX_INFO_JSON_BYTES:
                raise ValueError("yt-dlp info JSON exceeds the configured size limit")
            parsed: Any = json.loads(metadata_path.read_text(encoding="utf-8"))
            if not isinstance(parsed, dict):
                raise ValueError("yt-dlp info JSON is not an object")
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
            raise CreatorError(
                CreatorErrorCode.PROCESS_FAILED,
                "The video details could not be read. Try the URL again.",
                diagnostic=str(error),
            ) from error
        title = YtDlpPublicVideoDownloader._first_text(parsed, "track", "title")
        artist = YtDlpPublicVideoDownloader._first_text(
            parsed,
            "artist",
            "creator",
            "uploader",
            "channel",
        )
        return title, artist

    @staticmethod
    def _first_text(metadata: dict[str, Any], *keys: str) -> str | None:
        for key in keys:
            value = metadata.get(key)
            if isinstance(value, str):
                normalized = " ".join(value.split())[:MAX_METADATA_LENGTH].rstrip()
                if normalized:
                    return normalized
        return None
