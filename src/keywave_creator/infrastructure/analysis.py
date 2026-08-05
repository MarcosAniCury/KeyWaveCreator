"""Streaming audio feature extraction behind the application analysis port."""

from __future__ import annotations

import bisect
from pathlib import Path

import numpy as np
import soundfile as sf
from numpy.typing import NDArray
from scipy.signal import find_peaks

from keywave_creator.application.errors import CreatorError, CreatorErrorCode
from keywave_creator.application.models import CancellationToken
from keywave_creator.domain.analysis import AnalysisResult, FeaturePoint

FRAME_SIZE = 1024
MIN_BPM = 60.0
MAX_BPM = 200.0
SILENCE_FLOOR_BELOW_PEAK_DB = -42.0


class StreamingFeatureExtractor:
    """Extract bounded onset, beat, spectral, and sustain features.

    The implementation streams decoded frames through SoundFile and NumPy/SciPy so
    maximum-duration media does not need to be materialized in memory.
    """

    def analyze(
        self,
        audio_path: Path,
        *,
        expected_duration_ms: int,
        cancellation: CancellationToken,
    ) -> AnalysisResult:
        cancellation.raise_if_cancelled()
        try:
            sample_rate, duration_ms, rms, centroid_bias = self._stream_features(
                audio_path,
                cancellation,
            )
        except CreatorError:
            raise
        except Exception as error:
            raise CreatorError(
                CreatorErrorCode.ANALYSIS_FAILED,
                "The audio could not be analyzed.",
                diagnostic=str(error),
            ) from error

        if abs(duration_ms - expected_duration_ms) > 2_000:
            raise CreatorError(
                CreatorErrorCode.ANALYSIS_FAILED,
                "Decoded audio duration does not match the source metadata.",
            )
        if rms.size < 2 or float(np.max(rms)) < 1e-6:
            raise CreatorError(
                CreatorErrorCode.ANALYSIS_FAILED,
                "No usable rhythmic events were found in the audio.",
            )

        cancellation.raise_if_cancelled()
        log_energy = np.log1p(rms * 1000.0)
        onset_envelope = np.maximum(0.0, np.diff(log_energy, prepend=log_energy[0]))
        if onset_envelope.size >= 3:
            onset_envelope = np.convolve(
                onset_envelope,
                np.asarray((0.2, 0.6, 0.2), dtype=np.float64),
                mode="same",
            )
        positive = onset_envelope[onset_envelope > 0]
        if positive.size == 0:
            raise CreatorError(
                CreatorErrorCode.ANALYSIS_FAILED,
                "No usable rhythmic events were found in the audio.",
            )

        frame_rate = sample_rate / FRAME_SIZE
        prominence = max(float(np.std(onset_envelope)) * 0.35, float(np.median(positive)) * 0.5)
        peak_frames, properties = find_peaks(
            onset_envelope,
            distance=max(1, round(frame_rate * 0.085)),
            prominence=prominence,
        )
        if peak_frames.size == 0:
            peak_frames = np.asarray([int(np.argmax(onset_envelope))], dtype=np.int64)
        peak_strengths = properties.get("prominences")
        if not isinstance(peak_strengths, np.ndarray) or len(peak_strengths) != len(peak_frames):
            peak_strengths = onset_envelope[peak_frames]

        beat_frames, bpm, beat_consistency = self._estimate_beats(
            onset_envelope,
            np.asarray(peak_frames, dtype=np.int64),
            frame_rate,
        )
        fallback_strength = float(np.median(onset_envelope[peak_frames]))
        peak_set = {int(frame) for frame in peak_frames}
        frames = sorted(peak_set | beat_frames)
        silence_floor = max(
            1e-6,
            float(np.max(rms)) * 10 ** (SILENCE_FLOOR_BELOW_PEAK_DB / 20.0),
        )
        points: list[FeaturePoint] = []
        for frame in frames:
            cancellation.raise_if_cancelled()
            if float(rms[frame]) < silence_floor:
                continue
            time_ms = round(frame / frame_rate * 1000)
            if time_ms > duration_ms:
                continue
            strength = max(float(onset_envelope[frame]), fallback_strength * 0.7)
            points.append(
                FeaturePoint(
                    time_ms=time_ms,
                    strength=strength,
                    low_frequency_bias=float(centroid_bias[frame]),
                    sustain_ms=self._estimate_sustain_ms(rms, frame, frame_rate),
                    is_beat=frame in beat_frames,
                )
            )

        if not points:
            raise CreatorError(
                CreatorErrorCode.ANALYSIS_FAILED,
                "No usable rhythmic events were found in the audio.",
            )
        confidence = round(
            float(
                np.clip(
                    0.25
                    + 0.35 * min(1.0, len(points) / 64)
                    + 0.20 * min(1.0, len(beat_frames) / 16)
                    + 0.20 * beat_consistency,
                    0,
                    1,
                )
            ),
            4,
        )
        result = AnalysisResult(
            duration_ms=duration_ms,
            bpm=bpm,
            points=tuple(points),
            confidence=confidence,
        )
        result.validate()
        return result

    @staticmethod
    def _stream_features(
        audio_path: Path,
        cancellation: CancellationToken,
    ) -> tuple[int, int, NDArray[np.float64], NDArray[np.float64]]:
        rms_values: list[float] = []
        centroid_bias_values: list[float] = []
        spectral_data: dict[int, tuple[NDArray[np.float64], NDArray[np.float64]]] = {}
        with sf.SoundFile(audio_path) as audio:
            sample_rate = int(audio.samplerate)
            duration_ms = round(len(audio) / sample_rate * 1000)
            for frame_index, block_value in enumerate(
                audio.blocks(blocksize=FRAME_SIZE, dtype="float32", always_2d=True)
            ):
                if frame_index % 128 == 0:
                    cancellation.raise_if_cancelled()
                block = np.asarray(block_value, dtype=np.float64)
                mono = np.mean(block, axis=1)
                if mono.size == 0:
                    continue
                rms_values.append(float(np.sqrt(np.mean(np.square(mono)))))
                if len(mono) in spectral_data:
                    window, frequencies = spectral_data[len(mono)]
                else:
                    window = np.asarray(np.hanning(len(mono)), dtype=np.float64)
                    frequencies = np.asarray(
                        np.fft.rfftfreq(len(mono), d=1.0 / sample_rate),
                        dtype=np.float64,
                    )
                    spectral_data[len(mono)] = (window, frequencies)
                spectrum = np.abs(np.fft.rfft(mono * window))
                magnitude = float(np.sum(spectrum))
                if magnitude <= 1e-12:
                    centroid_bias_values.append(0.5)
                else:
                    centroid = float(np.sum(frequencies * spectrum) / magnitude)
                    centroid_bias_values.append(
                        float(np.clip(1.0 - centroid / (sample_rate / 2), 0, 1))
                    )
        return (
            sample_rate,
            duration_ms,
            np.asarray(rms_values, dtype=np.float64),
            np.asarray(centroid_bias_values, dtype=np.float64),
        )

    @staticmethod
    def _estimate_beats(
        envelope: NDArray[np.float64],
        peaks: NDArray[np.int64],
        frame_rate: float,
    ) -> tuple[set[int], float, float]:
        minimum_lag = max(1, round(frame_rate * 60 / MAX_BPM))
        maximum_lag = min(len(envelope) - 1, round(frame_rate * 60 / MIN_BPM))
        if maximum_lag < minimum_lag:
            return {int(peaks[0])}, 120.0, 0.0

        centered = envelope - float(np.mean(envelope))
        scores = [
            float(np.dot(centered[:-lag], centered[lag:]))
            for lag in range(minimum_lag, maximum_lag + 1)
        ]
        maximum_score = max(scores)
        tolerance = abs(maximum_score) * 0.08
        near_maximum = [
            minimum_lag + index
            for index, score in enumerate(scores)
            if score >= maximum_score - tolerance
        ]
        best_lag = min(near_maximum)
        bpm = float(np.clip(60 * frame_rate / best_lag, MIN_BPM, MAX_BPM))

        if len(peaks) >= 4:
            peak_intervals = np.diff(peaks.astype(np.float64))
            median_interval = float(np.median(peak_intervals))
            if median_interval > 0:
                relative_deviation = float(
                    np.median(np.abs(peak_intervals - median_interval)) / median_interval
                )
                interval_bpm = 60 * frame_rate / median_interval
                if relative_deviation <= 0.15 and MIN_BPM <= interval_bpm <= MAX_BPM:
                    best_lag = max(1, round(median_interval))
                    bpm = float(interval_bpm)

        peak_list = [int(frame) for frame in peaks]
        anchor = max(peak_list, key=lambda frame: float(envelope[frame]))
        targets = list(range(anchor, len(envelope), best_lag))
        targets.extend(range(anchor - best_lag, -1, -best_lag))
        snap_radius = max(1, round(best_lag * 0.2))
        beats: set[int] = set()
        deviations: list[float] = []
        for target in targets:
            insertion = bisect.bisect_left(peak_list, target)
            neighbors = peak_list[max(0, insertion - 1) : min(len(peak_list), insertion + 2)]
            nearest = min(neighbors, key=lambda frame: abs(frame - target)) if neighbors else target
            if abs(nearest - target) <= snap_radius:
                beats.add(nearest)
                deviations.append(abs(nearest - target) / best_lag)
        consistency = max(0.0, 1.0 - float(np.mean(deviations)) * 3) if deviations else 0.0
        return beats, bpm, consistency

    @staticmethod
    def _estimate_sustain_ms(
        rms: NDArray[np.float64],
        frame: int,
        frame_rate: float,
    ) -> int:
        baseline = float(rms[frame])
        if baseline <= 0:
            return 0
        threshold = baseline * 0.58
        end = frame
        maximum_frames = round(frame_rate * 2.0)
        while end + 1 < len(rms) and end - frame < maximum_frames:
            if float(rms[end + 1]) < threshold:
                break
            end += 1
        return round((end - frame) / frame_rate * 1000)
