"""Typed request, progress, cancellation, and media boundary models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from threading import Event

from keywave_creator.domain.media_timeline import CanonicalAudioTimeline

from .errors import CreatorError, CreatorErrorCode


class SourceKind(StrEnum):
    LOCAL = "local"
    YOUTUBE = "youtube"


class InputSourceKind(StrEnum):
    """Kinds accepted by the Creator's URL queue before media acquisition."""

    YOUTUBE_VIDEO = "youtube_video"
    YOUTUBE_PLAYLIST = "youtube_playlist"
    SPOTIFY_PLAYLIST = "spotify_playlist"


class ProgressStage(StrEnum):
    VALIDATING = "validating"
    ACQUIRING = "acquiring"
    NORMALIZING = "normalizing"
    ANALYZING = "analyzing"
    GENERATING = "generating"
    PACKAGING = "packaging"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class ProgressUpdate:
    stage: ProgressStage
    fraction: float
    message: str


@dataclass(frozen=True, slots=True)
class CreateLevelRequest:
    source_kind: SourceKind
    source: str
    title: str = ""
    artist: str = ""
    destination: Path | None = None
    seed: str = "keywave-v1"
    output_directory: Path | None = None
    include_video: bool = True

    def validate(self) -> None:
        if not self.source.strip():
            raise CreatorError(CreatorErrorCode.INVALID_SOURCE, "Choose a media source.")
        if len(self.title.strip()) > 200:
            raise CreatorError(
                CreatorErrorCode.INVALID_SOURCE,
                "The title cannot exceed 200 characters.",
            )
        if len(self.artist.strip()) > 200:
            raise CreatorError(
                CreatorErrorCode.INVALID_SOURCE,
                "The artist cannot exceed 200 characters.",
            )
        if self.source_kind is SourceKind.LOCAL and (
            not self.title.strip() or not self.artist.strip()
        ):
            raise CreatorError(
                CreatorErrorCode.INVALID_SOURCE,
                "Local media requires a title and artist.",
            )
        if (self.destination is None) == (self.output_directory is None):
            raise CreatorError(
                CreatorErrorCode.INVALID_SOURCE,
                "Choose either an output file or an output folder.",
            )
        if not self.seed or len(self.seed) > 128:
            raise CreatorError(
                CreatorErrorCode.INVALID_SOURCE,
                "The generation seed must contain between 1 and 128 characters.",
            )


@dataclass(frozen=True, slots=True)
class MediaInfo:
    duration_ms: int
    has_audio: bool
    has_video: bool
    width: int | None = None
    height: int | None = None
    frames_per_second: float | None = None
    audio_start_us: int = 0
    video_start_us: int = 0


@dataclass(frozen=True, slots=True)
class AcquiredMedia:
    path: Path
    source_id: str | None = None
    title: str | None = None
    artist: str | None = None


@dataclass(frozen=True, slots=True)
class PlaylistTrack:
    """One catalog track used only to locate an authorized public video."""

    title: str
    artist: str
    duration_ms: int | None = None


@dataclass(frozen=True, slots=True)
class ResolvedPublicVideo:
    """One canonical YouTube video ready for the normal Creator pipeline."""

    url: str
    label: str


@dataclass(frozen=True, slots=True)
class SourceExpansionProgress:
    """Bounded progress for resolving one playlist into public videos."""

    completed_items: int
    total_items: int
    message: str


@dataclass(frozen=True, slots=True)
class SourceExpansionResult:
    """Summary of a playlist expansion operation."""

    total_items: int
    resolved_items: int
    skipped_items: int


@dataclass(frozen=True, slots=True)
class NormalizedMedia:
    audio_path: Path
    analysis_path: Path
    video_path: Path | None
    cover_path: Path | None
    timeline: CanonicalAudioTimeline


@dataclass(frozen=True, slots=True)
class CreationResult:
    destination: Path
    package_id: str
    note_counts: tuple[int, int, int, int]
    bpm: float
    confidence: float
    title: str = ""
    artist: str = ""


class CancellationToken:
    """Provide cooperative cancellation without coupling to Qt."""

    def __init__(self) -> None:
        self._event = Event()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()

    def wait(self, timeout_seconds: float) -> bool:
        return self._event.wait(timeout_seconds)

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled:
            raise CreatorError(CreatorErrorCode.CANCELLED, "Creation was cancelled.")
