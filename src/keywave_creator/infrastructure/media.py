"""FFmpeg/ffprobe media validation and deterministic normalization."""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from numpy.typing import NDArray

from keywave_creator.application.errors import CreatorError, CreatorErrorCode
from keywave_creator.application.models import CancellationToken, MediaInfo, NormalizedMedia
from keywave_creator.domain.media_timeline import AutomaticSyncReport, CanonicalAudioTimeline

from .process import ProcessRunner, ToolLocator

LOGGER = logging.getLogger(__name__)
CANONICAL_SAMPLE_RATE_HZ = 48_000
ENVELOPE_SAMPLE_RATE_HZ = 1_000
MAXIMUM_SYNC_OFFSET_MS = 250
CORRELATION_WINDOW_MS = 2_000
SYNC_WINDOW_COUNT = 3


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
            audio_stream = next(
                (
                    item
                    for item in streams
                    if isinstance(item, dict) and item.get("codec_type") == "audio"
                ),
                None,
            )
            format_value = payload.get("format")
            format_start_seconds = self._optional_seconds(
                format_value.get("start_time") if isinstance(format_value, dict) else None
            )
            audio_start_us = self._start_time_us(audio_stream, format_start_seconds)
            video_start_us = (
                self._start_time_us(video_stream, format_start_seconds)
                if video_stream is not None
                else audio_start_us
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
            audio_start_us=audio_start_us,
            video_start_us=video_start_us,
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
        analysis_path = working_directory / "canonical-analysis.wav"
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
                "-filter_complex",
                (
                    "[0:a:0]aresample=48000:async=0:first_pts=0,"
                    "aformat=sample_fmts=flt:channel_layouts=stereo,"
                    "asetpts=N/SR/TB,asplit=2[playback][analysis_stereo];"
                    "[analysis_stereo]pan=mono|c0=0.5*c0+0.5*c1[analysis]"
                ),
                "-map",
                "[playback]",
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
                "-map",
                "[analysis]",
                "-vn",
                "-c:a",
                "pcm_s16le",
                "-ar",
                str(CANONICAL_SAMPLE_RATE_HZ),
                "-ac",
                "1",
                "-map_metadata",
                "-1",
                analysis_path,
            ),
            timeout_seconds=timeout_seconds,
            cancellation=cancellation,
        )
        if (
            not audio_path.is_file()
            or audio_path.stat().st_size == 0
            or not analysis_path.is_file()
            or analysis_path.stat().st_size == 0
        ):
            raise CreatorError(
                CreatorErrorCode.PROCESS_FAILED,
                "FFmpeg did not create the canonical audio timeline.",
            )

        playback_analysis_path = working_directory / "playback-analysis.wav"
        self._runner.run(
            (
                self._ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                audio_path,
                "-map",
                "0:a:0",
                "-vn",
                "-af",
                "aresample=48000:async=0:first_pts=0,pan=mono|c0=0.5*c0+0.5*c1",
                "-c:a",
                "pcm_s16le",
                "-ar",
                str(CANONICAL_SAMPLE_RATE_HZ),
                "-ac",
                "1",
                playback_analysis_path,
            ),
            timeout_seconds=timeout_seconds,
            cancellation=cancellation,
        )
        sample_rate_hz, sample_count = self._audio_shape(analysis_path)
        if sample_rate_hz != CANONICAL_SAMPLE_RATE_HZ or sample_count <= 0:
            raise CreatorError(
                CreatorErrorCode.PROCESS_FAILED,
                "The normalized audio timeline is invalid.",
            )
        sync_report = self._measure_sync(
            analysis_path,
            playback_analysis_path,
            cancellation,
        )
        timeline = CanonicalAudioTimeline(
            sample_rate_hz=sample_rate_hz,
            sample_count=sample_count,
            source_audio_start_us=info.audio_start_us,
            source_video_start_us=info.video_start_us,
            sync_report=sync_report,
        )
        timeline.validate()

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
                            "setpts=PTS-STARTPTS,scale=w=min(1920\\,iw):h=min(1080\\,ih):"
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
            analysis_path=analysis_path,
            video_path=video_path,
            cover_path=cover_path,
            timeline=timeline,
        )

    @staticmethod
    def _audio_shape(path: Path) -> tuple[int, int]:
        try:
            with sf.SoundFile(path) as audio:
                return int(audio.samplerate), len(audio)
        except (OSError, RuntimeError) as error:
            raise CreatorError(
                CreatorErrorCode.PROCESS_FAILED,
                "The normalized audio timeline could not be read.",
                diagnostic=str(error),
            ) from error

    @staticmethod
    def _measure_sync(
        canonical_path: Path,
        playback_path: Path,
        cancellation: CancellationToken,
    ) -> AutomaticSyncReport:
        canonical = _build_energy_envelope(canonical_path, cancellation)
        playback = _build_energy_envelope(playback_path, cancellation)
        if min(canonical.size, playback.size) < 10:
            return AutomaticSyncReport(0, 0, 0.0)

        window_length = min(
            CORRELATION_WINDOW_MS,
            max(10, min(canonical.size, playback.size) // 3),
        )
        starts = _select_high_information_windows(canonical, window_length)
        lag_values: list[float] = []
        score_total = 0.0
        for start in starts:
            cancellation.raise_if_cancelled()
            lag, score = _correlate_envelopes(
                canonical,
                playback,
                start,
                window_length,
            )
            lag_values.append(lag)
            score_total += max(0.0, score)
        lag_values.sort()
        median_lag = lag_values[len(lag_values) // 2]
        drift_ms = max(lag_values) - min(lag_values) if len(lag_values) > 1 else 0.0
        report = AutomaticSyncReport(
            analysis_to_playback_offset_us=round(median_lag * 1_000),
            drift_us=max(0, round(drift_ms * 1_000)),
            confidence=min(1.0, score_total / len(starts)),
        )
        report.validate()
        return report

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

    @staticmethod
    def _optional_seconds(value: object) -> float | None:
        if value is None:
            return None
        if not isinstance(value, (str, int, float)):
            return None
        result = float(value)
        return result if math.isfinite(result) else None

    @classmethod
    def _start_time_us(
        cls,
        stream: dict[str, Any] | None,
        fallback_seconds: float | None,
    ) -> int:
        stream_seconds = cls._optional_seconds(stream.get("start_time") if stream else None)
        seconds = stream_seconds if stream_seconds is not None else fallback_seconds
        return round((seconds or 0.0) * 1_000_000)


def _build_energy_envelope(
    path: Path,
    cancellation: CancellationToken,
) -> NDArray[np.float64]:
    values: list[NDArray[np.float64]] = []
    pending = np.empty(0, dtype=np.float64)
    with sf.SoundFile(path) as audio:
        sample_rate = int(audio.samplerate)
        if sample_rate != CANONICAL_SAMPLE_RATE_HZ:
            raise CreatorError(
                CreatorErrorCode.PROCESS_FAILED,
                "The normalized audio sample rate is invalid.",
            )
        samples_per_period = sample_rate // ENVELOPE_SAMPLE_RATE_HZ
        while True:
            cancellation.raise_if_cancelled()
            block_value = audio.read(sample_rate, dtype="float32", always_2d=True)
            if block_value.size == 0:
                break
            mono = np.mean(np.asarray(block_value, dtype=np.float64), axis=1)
            combined = np.concatenate((pending, mono)) if pending.size else mono
            complete = combined.size // samples_per_period * samples_per_period
            if complete:
                values.append(
                    np.mean(
                        np.abs(combined[:complete].reshape(-1, samples_per_period)),
                        axis=1,
                    )
                )
            pending = combined[complete:]
    if pending.size:
        values.append(np.asarray([float(np.mean(np.abs(pending)))], dtype=np.float64))
    return np.concatenate(values) if values else np.empty(0, dtype=np.float64)


def _select_high_information_windows(
    envelope: NDArray[np.float64],
    window_length: int,
) -> tuple[int, ...]:
    available = max(1, envelope.size - window_length)
    count = min(SYNC_WINDOW_COUNT, max(1, envelope.size // window_length))
    starts: list[int] = []
    for section in range(count):
        section_start = section * available // count
        section_end = (section + 1) * available // count
        candidates = range(section_start, section_end + 1, 250)
        best = max(
            candidates,
            key=lambda start: float(
                np.var(envelope[min(start, envelope.size - window_length) :][:window_length])
            ),
        )
        starts.append(min(best, envelope.size - window_length))
    return tuple(starts)


def _correlate_envelopes(
    canonical: NDArray[np.float64],
    playback: NDArray[np.float64],
    canonical_start: int,
    window_length: int,
) -> tuple[float, float]:
    minimum_lag = max(-MAXIMUM_SYNC_OFFSET_MS, -canonical_start)
    maximum_lag = min(
        MAXIMUM_SYNC_OFFSET_MS,
        playback.size - canonical_start - window_length,
    )
    if minimum_lag > maximum_lag:
        return 0.0, 0.0
    left = canonical[canonical_start : canonical_start + window_length]
    left = left - float(np.mean(left))
    left_energy = float(np.dot(left, left))
    scores = np.zeros(maximum_lag - minimum_lag + 1, dtype=np.float64)
    for score_index, lag in enumerate(range(minimum_lag, maximum_lag + 1)):
        right = playback[canonical_start + lag : canonical_start + lag + window_length]
        right = right - float(np.mean(right))
        denominator = math.sqrt(left_energy * float(np.dot(right, right)))
        scores[score_index] = (
            0.0 if denominator <= 1e-12 else float(np.dot(left, right)) / denominator
        )
    best_index = int(np.argmax(scores))
    refined_index = float(best_index)
    if 0 < best_index < scores.size - 1:
        left_score, center, right_score = scores[best_index - 1 : best_index + 2]
        divisor = left_score - 2 * center + right_score
        if abs(float(divisor)) > 1e-7:
            refined_index += float(np.clip(0.5 * (left_score - right_score) / divisor, -0.5, 0.5))
    return minimum_lag + refined_index, float(scores[best_index])


def _is_windows() -> bool:
    import os

    return os.name == "nt"
