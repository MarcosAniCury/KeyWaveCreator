"""FFmpeg/ffprobe media validation and deterministic normalization."""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any

from keywave_creator.application.errors import CreatorError, CreatorErrorCode
from keywave_creator.application.models import CancellationToken, MediaInfo, NormalizedMedia

from .process import ProcessRunner, ToolLocator

LOGGER = logging.getLogger(__name__)


class FFmpegMediaProcessor:
    """Adapt reviewed FFmpeg binaries to the Creator media port."""

    def __init__(
        self,
        *,
        runner: ProcessRunner | None = None,
        locator: ToolLocator | None = None,
    ) -> None:
        tool_locator = locator or ToolLocator()
        self._ffmpeg = tool_locator.resolve("ffmpeg.exe" if _is_windows() else "ffmpeg")
        self._ffprobe = tool_locator.resolve("ffprobe.exe" if _is_windows() else "ffprobe")
        self._runner = runner or ProcessRunner()

    def probe(self, path: Path, cancellation: CancellationToken) -> MediaInfo:
        result = self._runner.run(
            (
                self._ffprobe,
                "-v",
                "error",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                path,
            ),
            timeout_seconds=60,
            cancellation=cancellation,
        )
        try:
            payload: dict[str, Any] = json.loads(result.stdout)
            streams = payload.get("streams", [])
            if not isinstance(streams, list):
                raise TypeError("streams is not a list")
            duration_seconds = self._duration_seconds(payload, streams)
            video_stream = next(
                (
                    item
                    for item in streams
                    if isinstance(item, dict) and item.get("codec_type") == "video"
                ),
                None,
            )
            has_audio = any(
                isinstance(item, dict) and item.get("codec_type") == "audio" for item in streams
            )
            width = (
                int(video_stream["width"]) if video_stream and video_stream.get("width") else None
            )
            height = (
                int(video_stream["height"]) if video_stream and video_stream.get("height") else None
            )
            fps = (
                self._parse_fraction(str(video_stream.get("avg_frame_rate", "0/1")))
                if video_stream
                else None
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise CreatorError(
                CreatorErrorCode.UNSUPPORTED_MEDIA,
                "Could not read the media metadata.",
                diagnostic=str(error),
            ) from error

        duration_ms = round(duration_seconds * 1000)
        return MediaInfo(
            duration_ms=duration_ms,
            has_audio=has_audio,
            has_video=video_stream is not None,
            width=width,
            height=height,
            frames_per_second=fps,
        )

    def normalize(
        self,
        source: Path,
        info: MediaInfo,
        working_directory: Path,
        *,
        include_video: bool,
        cancellation: CancellationToken,
    ) -> NormalizedMedia:
        timeout_seconds = min(4 * 60 * 60, max(120, info.duration_ms / 1000 * 5))
        audio_path = working_directory / "song.ogg"
        self._runner.run(
            (
                self._ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                source,
                "-map",
                "0:a:0",
                "-vn",
                "-c:a",
                "libvorbis",
                "-q:a",
                "5",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-map_metadata",
                "-1",
                audio_path,
            ),
            timeout_seconds=timeout_seconds,
            cancellation=cancellation,
        )
        if not audio_path.is_file() or audio_path.stat().st_size == 0:
            raise CreatorError(
                CreatorErrorCode.PROCESS_FAILED,
                "FFmpeg did not create normalized audio.",
            )

        video_path: Path | None = None
        cover_path: Path | None = None
        if info.has_video:
            if include_video:
                video_path = working_directory / "background.webm"
                self._runner.run(
                    (
                        self._ffmpeg,
                        "-nostdin",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-i",
                        source,
                        "-map",
                        "0:v:0",
                        "-an",
                        "-vf",
                        (
                            "scale=w=min(1920\\,iw):h=min(1080\\,ih):"
                            "force_original_aspect_ratio=decrease,fps=30,format=yuv420p"
                        ),
                        "-c:v",
                        "libvpx",
                        "-deadline",
                        "good",
                        "-cpu-used",
                        "2",
                        "-crf",
                        "24",
                        "-b:v",
                        "0",
                        "-map_metadata",
                        "-1",
                        video_path,
                    ),
                    timeout_seconds=timeout_seconds,
                    cancellation=cancellation,
                )
            cover_source = video_path or source
            cover_path = self._create_cover(
                cover_source,
                working_directory,
                duration_ms=info.duration_ms,
                cancellation=cancellation,
            )

        return NormalizedMedia(
            audio_path=audio_path,
            video_path=video_path,
            cover_path=cover_path,
        )

    def _create_cover(
        self,
        source: Path,
        working_directory: Path,
        *,
        duration_ms: int,
        cancellation: CancellationToken,
    ) -> Path | None:
        cover_path = working_directory / "cover.jpg"
        seek_seconds = min(10.0, max(0.0, duration_ms / 3000))
        try:
            self._runner.run(
                (
                    self._ffmpeg,
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-ss",
                    f"{seek_seconds:.3f}",
                    "-i",
                    source,
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=w=min(1280\\,iw):h=min(720\\,ih):force_original_aspect_ratio=decrease",
                    "-q:v",
                    "3",
                    "-map_metadata",
                    "-1",
                    cover_path,
                ),
                timeout_seconds=90,
                cancellation=cancellation,
            )
        except CreatorError as error:
            if error.code in {CreatorErrorCode.PROCESS_FAILED, CreatorErrorCode.PROCESS_TIMEOUT}:
                LOGGER.warning("Optional cover extraction failed: %s", error.diagnostic or error)
                return None
            raise
        return cover_path if cover_path.is_file() and cover_path.stat().st_size else None

    @staticmethod
    def _duration_seconds(payload: dict[str, Any], streams: list[Any]) -> float:
        format_value = payload.get("format")
        candidates: list[float] = []
        if isinstance(format_value, dict) and format_value.get("duration") is not None:
            candidates.append(float(format_value["duration"]))
        for stream in streams:
            if isinstance(stream, dict) and stream.get("duration") is not None:
                candidates.append(float(stream["duration"]))
        finite = [value for value in candidates if math.isfinite(value) and value > 0]
        if not finite:
            raise ValueError("No positive finite duration")
        return max(finite)

    @staticmethod
    def _parse_fraction(value: str) -> float | None:
        numerator_text, separator, denominator_text = value.partition("/")
        if not separator:
            result = float(value)
        else:
            denominator = float(denominator_text)
            result = float(numerator_text) / denominator if denominator else 0.0
        return result if math.isfinite(result) and result > 0 else None


def _is_windows() -> bool:
    import os

    return os.name == "nt"
