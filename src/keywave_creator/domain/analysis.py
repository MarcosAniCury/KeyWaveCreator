"""Immutable output of audio feature extraction."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True, order=True)
class FeaturePoint:
    """Represent one musically relevant instant on the normalized timeline."""

    time_ms: int
    strength: float
    low_frequency_bias: float
    sustain_ms: int
    is_beat: bool = False

    def validate(self, *, duration_ms: int) -> None:
        if self.time_ms < 0 or self.time_ms > duration_ms:
            raise ValueError("Feature time is outside the media duration.")
        if not math.isfinite(self.strength) or self.strength < 0:
            raise ValueError("Feature strength must be finite and non-negative.")
        if not math.isfinite(self.low_frequency_bias) or not 0 <= self.low_frequency_bias <= 1:
            raise ValueError("Frequency bias must be within 0..1.")
        if self.sustain_ms < 0:
            raise ValueError("Feature sustain must be non-negative.")


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    """Contain deterministic features extracted from one normalized audio file."""

    duration_ms: int
    bpm: float
    points: tuple[FeaturePoint, ...]
    confidence: float

    def validate(self) -> None:
        if self.duration_ms <= 0:
            raise ValueError("Analysis duration must be positive.")
        if not math.isfinite(self.bpm) or self.bpm <= 0:
            raise ValueError("Estimated BPM must be finite and positive.")
        if not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1:
            raise ValueError("Analysis confidence must be within 0..1.")
        if not self.points:
            raise ValueError("Analysis did not find any musical events.")
        if self.points != tuple(sorted(self.points, key=lambda point: point.time_ms)):
            raise ValueError("Feature points must be sorted by time.")
        previous_time = -1
        for point in self.points:
            point.validate(duration_ms=self.duration_ms)
            if point.time_ms == previous_time:
                raise ValueError("Feature points must have unique timestamps.")
            previous_time = point.time_ms
