"""Interfaces owned by the Creator application layer."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from keywave_creator.domain.analysis import AnalysisResult

from .models import AcquiredMedia, CancellationToken, MediaInfo, NormalizedMedia, ProgressUpdate

ProgressSink = Callable[[ProgressUpdate], None]


class PublicVideoDownloader(Protocol):
    def download(
        self,
        url: str,
        working_directory: Path,
        cancellation: CancellationToken,
    ) -> AcquiredMedia: ...


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
