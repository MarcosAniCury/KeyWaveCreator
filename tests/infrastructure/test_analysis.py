from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from keywave_creator.application.models import CancellationToken
from keywave_creator.infrastructure.analysis import StreamingFeatureExtractor


def test_streaming_analyzer_detects_synthetic_pulse_train(tmp_path: Path) -> None:
    sample_rate = 48_000
    duration_seconds = 8
    times = np.arange(sample_rate * duration_seconds, dtype=np.float64) / sample_rate
    pulse_phase = np.mod(times, 0.5)
    samples = np.where(
        pulse_phase < 0.08,
        0.8 * np.sin(2 * np.pi * 440 * times),
        0.0,
    ).astype(np.float32)
    audio_path = tmp_path / "pulse.wav"
    sf.write(audio_path, samples, sample_rate, subtype="PCM_16")

    result = StreamingFeatureExtractor().analyze(
        audio_path,
        expected_duration_ms=duration_seconds * 1000,
        cancellation=CancellationToken(),
    )

    assert 110 <= result.bpm <= 130
    assert len(result.points) >= 12
    assert result.points == tuple(sorted(result.points, key=lambda point: point.time_ms))
    assert 0 <= result.confidence <= 1


def test_streaming_analyzer_rejects_silence(tmp_path: Path) -> None:
    audio_path = tmp_path / "silence.wav"
    sf.write(audio_path, np.zeros(48_000, dtype=np.float32), 48_000, subtype="PCM_16")

    from keywave_creator.application.errors import CreatorError, CreatorErrorCode

    try:
        StreamingFeatureExtractor().analyze(
            audio_path,
            expected_duration_ms=1000,
            cancellation=CancellationToken(),
        )
    except CreatorError as error:
        assert error.code is CreatorErrorCode.ANALYSIS_FAILED
    else:
        raise AssertionError("Silent media must be rejected")


def test_streaming_analyzer_does_not_invent_beats_inside_silent_breaks(
    tmp_path: Path,
) -> None:
    sample_rate = 48_000
    duration_seconds = 8
    times = np.arange(sample_rate * duration_seconds, dtype=np.float64) / sample_rate
    pulse_phase = np.mod(times, 0.5)
    is_silent_break = (times >= 2.0) & (times < 5.0)
    samples = np.where(
        (pulse_phase < 0.08) & ~is_silent_break,
        0.8 * np.sin(2 * np.pi * 440 * times),
        0.0,
    ).astype(np.float32)
    audio_path = tmp_path / "pulse-with-break.wav"
    sf.write(audio_path, samples, sample_rate, subtype="PCM_16")

    result = StreamingFeatureExtractor().analyze(
        audio_path,
        expected_duration_ms=duration_seconds * 1000,
        cancellation=CancellationToken(),
    )

    assert any(point.time_ms < 2_000 for point in result.points)
    assert any(point.time_ms >= 5_000 for point in result.points)
    assert not [point for point in result.points if 2_150 <= point.time_ms < 4_850]
