"""Sample-accurate contracts shared by media normalization and packaging."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AutomaticSyncReport:
    """Describe the measured relationship between analysis PCM and playable audio."""

    analysis_to_playback_offset_us: int
    drift_us: int
    confidence: float

    def validate(self) -> None:
        if abs(self.analysis_to_playback_offset_us) > 250_000:
            raise ValueError("Automatic audio offset exceeds the bounded search range.")
        if self.drift_us < 0:
            raise ValueError("Measured audio drift must be non-negative.")
        if not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1:
            raise ValueError("Automatic sync confidence must be within 0..1.")


@dataclass(frozen=True, slots=True)
class CanonicalAudioTimeline:
    """Define sample zero for analysis, encoded audio, charts, and video."""

    sample_rate_hz: int
    sample_count: int
    source_audio_start_us: int
    source_video_start_us: int
    sync_report: AutomaticSyncReport

    def validate(self) -> None:
        if self.sample_rate_hz <= 0:
            raise ValueError("Canonical sample rate must be positive.")
        if self.sample_count <= 0:
            raise ValueError("Canonical sample count must be positive.")
        self.sync_report.validate()

    @property
    def duration_us(self) -> int:
        return round(self.sample_count * 1_000_000 / self.sample_rate_hz)

    @property
    def duration_ms(self) -> int:
        return round(self.sample_count * 1_000 / self.sample_rate_hz)

    @property
    def video_offset_us(self) -> int:
        return self.source_video_start_us - self.source_audio_start_us
