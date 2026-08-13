"""Restricted public single-video acquisition through pinned yt-dlp."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from keywave_creator.application.errors import CreatorError, CreatorErrorCode
from keywave_creator.application.models import (
    AcquiredMedia,
    CancellationToken,
    PlaylistTrack,
    ResolvedPublicVideo,
)

from .process import ProcessRunner, ToolLocator

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
PLAYLIST_ID = re.compile(r"^[A-Za-z0-9_-]{10,128}$")
MAX_URL_LENGTH = 2_048
MAX_INFO_JSON_BYTES = 4 * 1024 * 1024
MAX_METADATA_LENGTH = 200
MAX_PLAYLIST_ITEMS = 200
SEARCH_RESULT_LIMIT = 5
PRIMARY_VIDEO_FORMAT = "bestvideo[height<=1080][fps<=30]+bestaudio/best[height<=1080][fps<=30]"
PUBLIC_PROGRESSIVE_FALLBACK_FORMAT = "best[height<=720][fps<=30]/best[height<=1080][fps<=30]/best"
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
        ffmpeg_name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
        ffmpeg = self._locator.resolve(ffmpeg_name, environment_variable="KEYWAVE_FFMPEG")
        command = self._download_command(
            ffmpeg,
            working_directory,
            PRIMARY_VIDEO_FORMAT,
        )
        if deno := self._locator.optional("deno.exe" if sys.platform == "win32" else "deno"):
            command.extend(("--js-runtimes", f"deno:{deno}"))
        command.append(canonical_url)
        try:
            self._run_download(command, working_directory, cancellation)
        except CreatorError as primary_error:
            if not self._is_http_403(primary_error):
                raise
            self._clear_attempt_outputs(working_directory)
            fallback_command = self._download_command(
                ffmpeg,
                working_directory,
                PUBLIC_PROGRESSIVE_FALLBACK_FORMAT,
            )
            if deno is not None:
                fallback_command.extend(("--js-runtimes", f"deno:{deno}"))
            fallback_command.append(canonical_url)
            try:
                self._run_download(fallback_command, working_directory, cancellation)
            except CreatorError as fallback_error:
                if not self._is_http_403(fallback_error):
                    raise
                raise CreatorError(
                    CreatorErrorCode.PROCESS_FAILED,
                    "YouTube refused every public stream for this video. "
                    "Try again later or choose another public video for the same song.",
                    diagnostic=fallback_error.diagnostic,
                ) from fallback_error
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

    def _download_command(
        self,
        ffmpeg: Path,
        working_directory: Path,
        format_selector: str,
    ) -> list[Path | str]:
        return [
            *self._command_prefix(),
            "--no-config",
            "--no-update",
            "--no-playlist",
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
            format_selector,
            "--output",
            str(working_directory / "source.%(ext)s"),
        ]

    def _run_download(
        self,
        command: list[Path | str],
        working_directory: Path,
        cancellation: CancellationToken,
    ) -> None:
        self._runner.run(
            command,
            timeout_seconds=60 * 60,
            cancellation=cancellation,
            working_directory=working_directory,
        )

    @staticmethod
    def _is_http_403(error: CreatorError) -> bool:
        if error.code is not CreatorErrorCode.PROCESS_FAILED:
            return False
        diagnostic = (error.diagnostic or "").casefold()
        return "http error 403" in diagnostic or "403: forbidden" in diagnostic

    @staticmethod
    def _clear_attempt_outputs(working_directory: Path) -> None:
        try:
            for path in working_directory.glob("source*"):
                if path.is_file():
                    path.unlink()
        except OSError as error:
            raise CreatorError(
                CreatorErrorCode.PROCESS_FAILED,
                "The failed download could not be reset before retrying.",
                diagnostic=str(error),
            ) from error

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


class YtDlpYouTubePlaylistResolver:
    """Resolve a bounded public YouTube playlist to canonical video URLs."""

    def __init__(
        self,
        *,
        runner: ProcessRunner | None = None,
        locator: ToolLocator | None = None,
    ) -> None:
        self._runner = runner or ProcessRunner()
        self._locator = locator or ToolLocator()

    def resolve(
        self,
        url: str,
        cancellation: CancellationToken,
    ) -> tuple[ResolvedPublicVideo, ...]:
        playlist_id = self._validate_playlist_url(url)
        command = [
            *YtDlpPublicVideoDownloader(
                runner=self._runner,
                locator=self._locator,
            )._command_prefix(),
            "--no-config",
            "--no-update",
            "--no-cache-dir",
            "--no-warnings",
            "--skip-download",
            "--flat-playlist",
            "--dump-single-json",
            "--socket-timeout",
            "30",
            "--retries",
            "3",
            "--playlist-end",
            str(MAX_PLAYLIST_ITEMS + 1),
        ]
        self._append_javascript_runtime(command)
        command.append(f"https://www.youtube.com/playlist?list={playlist_id}")
        result = self._runner.run(
            command,
            timeout_seconds=5 * 60,
            cancellation=cancellation,
        )
        entries = _read_entries(result.stdout, "The YouTube playlist could not be read.")
        if len(entries) > MAX_PLAYLIST_ITEMS:
            raise CreatorError(
                CreatorErrorCode.INVALID_SOURCE,
                f"Playlists can contain at most {MAX_PLAYLIST_ITEMS} videos.",
            )
        videos: list[ResolvedPublicVideo] = []
        seen_ids: set[str] = set()
        for entry in entries:
            video_id = entry.get("id")
            if not isinstance(video_id, str) or not VIDEO_ID.fullmatch(video_id):
                continue
            if video_id in seen_ids:
                continue
            seen_ids.add(video_id)
            title = _first_text(entry, "title") or f"YouTube video {len(videos) + 1}"
            videos.append(
                ResolvedPublicVideo(
                    url=f"https://www.youtube.com/watch?v={video_id}",
                    label=title,
                )
            )
        if not videos:
            raise CreatorError(
                CreatorErrorCode.SOURCE_LOOKUP_FAILED,
                "The YouTube playlist has no public videos that KeyWave can process.",
            )
        return tuple(videos)

    @staticmethod
    def _validate_playlist_url(url: str) -> str:
        value = url.strip()
        if len(value) > MAX_URL_LENGTH:
            raise CreatorError(CreatorErrorCode.INVALID_SOURCE, "The YouTube URL is too long.")
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
                "Enter an HTTPS URL for one public YouTube playlist.",
            )
        values = parse_qs(parsed.query).get("list", [])
        playlist_id = values[0] if values else ""
        if not PLAYLIST_ID.fullmatch(playlist_id):
            raise CreatorError(
                CreatorErrorCode.INVALID_SOURCE,
                "The YouTube playlist URL is not valid.",
            )
        return playlist_id

    def _append_javascript_runtime(self, command: list[str | Path]) -> None:
        executable_name = "deno.exe" if sys.platform == "win32" else "deno"
        if deno := self._locator.optional(executable_name):
            command.extend(("--js-runtimes", f"deno:{deno}"))


class YtDlpYouTubeMusicVideoFinder:
    """Find one conservative YouTube match for Spotify catalog metadata."""

    def __init__(
        self,
        *,
        runner: ProcessRunner | None = None,
        locator: ToolLocator | None = None,
    ) -> None:
        self._runner = runner or ProcessRunner()
        self._locator = locator or ToolLocator()

    def find(
        self,
        track: PlaylistTrack,
        cancellation: CancellationToken,
    ) -> ResolvedPublicVideo | None:
        query = f"{track.artist} {track.title} official music video"
        command: list[str | Path] = [
            *YtDlpPublicVideoDownloader(
                runner=self._runner,
                locator=self._locator,
            )._command_prefix(),
            "--no-config",
            "--no-update",
            "--no-cache-dir",
            "--no-warnings",
            "--skip-download",
            "--flat-playlist",
            "--dump-single-json",
            "--socket-timeout",
            "30",
            "--retries",
            "3",
            f"ytsearch{SEARCH_RESULT_LIMIT}:{query}",
        ]
        executable_name = "deno.exe" if sys.platform == "win32" else "deno"
        if deno := self._locator.optional(executable_name):
            command[-1:-1] = ["--js-runtimes", f"deno:{deno}"]
        result = self._runner.run(
            command,
            timeout_seconds=2 * 60,
            cancellation=cancellation,
        )
        entries = _read_entries(result.stdout, "YouTube search could not be completed.")
        ranked = sorted(
            ((self._score(track, entry), index, entry) for index, entry in enumerate(entries)),
            key=lambda item: (-item[0], item[1]),
        )
        if not ranked or ranked[0][0] < 0.58:
            return None
        entry = ranked[0][2]
        video_id = entry.get("id")
        if not isinstance(video_id, str) or not VIDEO_ID.fullmatch(video_id):
            return None
        title = _first_text(entry, "title") or f"{track.artist} - {track.title}"
        return ResolvedPublicVideo(
            url=f"https://www.youtube.com/watch?v={video_id}",
            label=title,
        )

    @staticmethod
    def _score(track: PlaylistTrack, entry: dict[str, Any]) -> float:
        title = _first_text(entry, "title") or ""
        channel = _first_text(entry, "channel", "uploader") or ""
        expected_title = _tokens(track.title)
        expected_artist = _tokens(track.artist)
        candidate_title = _tokens(title)
        candidate_identity = candidate_title | _tokens(channel)
        if not expected_title or not expected_artist:
            return 0.0

        title_coverage = len(expected_title & candidate_title) / len(expected_title)
        artist_coverage = len(expected_artist & candidate_identity) / len(expected_artist)
        score = title_coverage * 0.52 + artist_coverage * 0.30

        duration = entry.get("duration")
        if track.duration_ms is not None and isinstance(duration, (int, float)):
            difference_ms = abs(int(float(duration) * 1_000) - track.duration_ms)
            score += max(0.0, 0.10 * (1.0 - difference_ms / 20_000.0))

        channel_tokens = _tokens(channel)
        if "official" in candidate_title or any("vevo" in token for token in channel_tokens):
            score += 0.08

        expected_modifiers = expected_title & _UNWANTED_MODIFIERS
        introduced_modifiers = (candidate_title & _UNWANTED_MODIFIERS) - expected_modifiers
        if introduced_modifiers:
            score -= 0.40
        return score


_UNWANTED_MODIFIERS = frozenset(
    {"cover", "karaoke", "reaction", "slowed", "sped", "nightcore", "remix", "instrumental"}
)


def _tokens(value: str) -> frozenset[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return frozenset(token for token in re.findall(r"[^\W_]+", normalized) if len(token) > 1)


def _read_entries(payload: str, public_message: str) -> tuple[dict[str, Any], ...]:
    try:
        parsed: Any = json.loads(payload)
        if not isinstance(parsed, dict):
            raise ValueError("yt-dlp output is not an object")
        raw_entries = parsed.get("entries")
        if not isinstance(raw_entries, list):
            raise ValueError("yt-dlp output has no entries array")
        entries = tuple(entry for entry in raw_entries if isinstance(entry, dict))
    except (json.JSONDecodeError, ValueError) as error:
        raise CreatorError(
            CreatorErrorCode.SOURCE_LOOKUP_FAILED,
            public_message,
            diagnostic=str(error),
        ) from error
    return entries


def _first_text(metadata: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, str):
            normalized = " ".join(value.split())[:MAX_METADATA_LENGTH].rstrip()
            if normalized:
                return normalized
    return None
