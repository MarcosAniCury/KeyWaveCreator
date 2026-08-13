"""Interfaces owned by the Creator application layer."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from keywave_creator.domain.analysis import AnalysisResult

from .models import (
    AcquiredMedia,
    CancellationToken,
    MediaInfo,
    NormalizedMedia,
    PlaylistTrack,
    ProgressUpdate,
    ResolvedPublicVideo,
)

ProgressSink = Callable[[ProgressUpdate], None]


class PublicVideoDownloader(Protocol):
    def download(
        self,
        url: str,
        working_directory: Path,
        cancellation: CancellationToken,
    ) -> AcquiredMedia: ...


class YouTubePlaylistResolver(Protocol):
    """Resolve a bounded YouTube playlist without downloading its media."""

    def resolve(
        self,
        url: str,
        cancellation: CancellationToken,
    ) -> tuple[ResolvedPublicVideo, ...]: ...


class SpotifyPlaylistResolver(Protocol):
    """Read playlist track metadata through an authorized Spotify session."""

    def resolve(
        self,
        url: str,
        client_id: str,
        cancellation: CancellationToken,
    ) -> tuple[PlaylistTrack, ...]: ...


class YouTubeMusicVideoFinder(Protocol):
    """Find a conservative YouTube music-video match for catalog metadata."""

    def find(
        self,
        track: PlaylistTrack,
        cancellation: CancellationToken,
    ) -> ResolvedPublicVideo | None: ...


class MediaProcessor(Protocol):
    def probe(self, path: Path, cancellation: CancellationToken) -> MediaInfo: ...

    def normalize(
        self,
        source: Path,
        info: MediaInfo,
        working_directory: Path,
        *,
        include_video: bool,
        cancellation: CancellationToken,
    ) -> NormalizedMedia: ...


class FeatureExtractor(Protocol):
    def analyze(
        self,
        audio_path: Path,
        *,
        expected_duration_ms: int,
        cancellation: CancellationToken,
    ) -> AnalysisResult: ...
