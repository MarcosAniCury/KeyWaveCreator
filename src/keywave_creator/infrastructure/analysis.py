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
HOP_SIZE = 512
MIN_BPM = 60.0
MAX_BPM = 200.0
SILENCE_FLOOR_BELOW_PEAK_DB = -42.0
MINIMUM_ONSET_INTERVAL_SECONDS = 0.115


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
            sample_rate, duration_ms, rms, centroid_bias, spectral_flux = self._stream_features(
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
        signed_energy_change = np.diff(log_energy, prepend=log_energy[0])
        energy_novelty = np.maximum(0.0, signed_energy_change)
        spectral_novelty = self._normalize_novelty(spectral_flux, relative_floor=0.005)
        spectral_novelty = np.where(signed_energy_change < -0.05, 0.0, spectral_novelty)
        onset_envelope = (
            0.28 * self._normalize_novelty(energy_novelty, relative_floor=0.03)
            + 0.72 * spectral_novelty
        )
        if onset_envelope.size >= 3:
            onset_envelope = np.convolve(
                onset_envelope,
                np.asarray((0.15, 0.70, 0.15), dtype=np.float64),
                mode="same",
            )
        positive = onset_envelope[onset_envelope > 0]
        if positive.size == 0:
            raise CreatorError(
                CreatorErrorCode.ANALYSIS_FAILED,
                "No usable rhythmic events were found in the audio.",
            )

        frame_rate = sample_rate / HOP_SIZE
        prominence = max(float(np.std(onset_envelope)) * 0.35, float(np.median(positive)) * 0.5)
        peak_frames, _ = find_peaks(
            onset_envelope,
            distance=max(1, round(frame_rate * MINIMUM_ONSET_INTERVAL_SECONDS)),
            prominence=prominence,
        )
        if peak_frames.size == 0:
            peak_frames = np.asarray([int(np.argmax(onset_envelope))], dtype=np.int64)

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
            # A feature represents the center of its overlapping analysis window. Using the
            # block start here makes every generated tile visibly early by half a window.
            time_ms = round((frame * HOP_SIZE + FRAME_SIZE / 2) / sample_rate * 1000)
            if time_ms > duration_ms:
                continue
            strength = max(float(onset_envelope[frame]), fallback_strength * 0.7)
            points.append(
                FeaturePoint(
                    time_ms=time_ms,
                    strength=strength,
                    low_frequency_bias=float(centroid_bias[frame]),
                    sustain_ms=self._estimate_sustain_ms(
                        rms,
                        onset_envelope,
                        frame,
                        frame_rate,
                        silence_floor,
                    ),
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
    ) -> tuple[
        int,
        int,
        NDArray[np.float64],
        NDArray[np.float64],
        NDArray[np.float64],
    ]:
        rms_values: list[float] = []
        centroid_bias_values: list[float] = []
        spectral_flux_values: list[float] = []
        window = np.asarray(np.hanning(FRAME_SIZE), dtype=np.float64)
        frequencies: NDArray[np.float64] | None = None
        frequency_weights: NDArray[np.float64] | None = None
        previous_log_spectrum: NDArray[np.float64] | None = None
        with sf.SoundFile(audio_path) as audio:
            sample_rate = int(audio.samplerate)
            duration_ms = round(len(audio) / sample_rate * 1000)
            frequencies = np.asarray(
                np.fft.rfftfreq(FRAME_SIZE, d=1.0 / sample_rate),
                dtype=np.float64,
            )
            frequency_weights = 0.65 + 0.35 * frequencies / (sample_rate / 2)
            for frame_index, block_value in enumerate(
                audio.blocks(
                    blocksize=FRAME_SIZE,
                    overlap=FRAME_SIZE - HOP_SIZE,
                    dtype="float32",
                    always_2d=True,
                )
            ):
                if frame_index % 128 == 0:
                    cancellation.raise_if_cancelled()
                block = np.asarray(block_value, dtype=np.float64)
                mono = np.mean(block, axis=1)
                if mono.size == 0:
                    continue
                rms_values.append(float(np.sqrt(np.mean(np.square(mono)))))
                if mono.size < FRAME_SIZE:
                    mono = np.pad(mono, (0, FRAME_SIZE - mono.size))
                spectrum = np.abs(np.fft.rfft(mono * window))
                magnitude = float(np.sum(spectrum))
                if magnitude <= 1e-12:
                    centroid_bias_values.append(0.5)
                else:
                    assert frequencies is not None
                    centroid = float(np.sum(frequencies * spectrum) / magnitude)
                    centroid_bias_values.append(
                        float(np.clip(1.0 - centroid / (sample_rate / 2), 0, 1))
                    )
                # Compare spectral shape, not raw loudness. Slow volume swells otherwise look
                # like playable attacks even though no new instrument or articulation occurred.
                normalized_spectrum = spectrum / max(magnitude, 1e-12)
                log_spectrum = np.log1p(normalized_spectrum * 1000.0)
                if previous_log_spectrum is None:
                    spectral_flux_values.append(0.0)
                else:
                    assert frequency_weights is not None
                    positive_change = np.maximum(0.0, log_spectrum - previous_log_spectrum)
                    spectral_flux_values.append(float(np.mean(positive_change * frequency_weights)))
                previous_log_spectrum = log_spectrum
        return (
            sample_rate,
            duration_ms,
            np.asarray(rms_values, dtype=np.float64),
            np.asarray(centroid_bias_values, dtype=np.float64),
            np.asarray(spectral_flux_values, dtype=np.float64),
        )

    @staticmethod
    def _normalize_novelty(
        values: NDArray[np.float64],
        *,
        relative_floor: float = 0.0,
    ) -> NDArray[np.float64]:
        floor = float(np.max(values)) * relative_floor if values.size else 0.0
        filtered = np.where(values >= floor, values, 0.0)
        positive = filtered[filtered > 0]
        if positive.size == 0:
            return np.zeros_like(values)
        scale = float(np.percentile(positive, 90))
        if scale <= 1e-12:
            return np.zeros_like(values)
        return np.clip(filtered / scale, 0.0, 4.0)

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
        onset_envelope: NDArray[np.float64],
        frame: int,
        frame_rate: float,
        silence_floor: float,
    ) -> int:
        local_end = min(len(rms), frame + max(2, round(frame_rate * 0.08)))
        baseline = float(np.percentile(rms[frame:local_end], 75))
        if baseline <= 0:
            return 0
        threshold = max(silence_floor, baseline * 0.42)
        positive_novelty = onset_envelope[onset_envelope > 0]
        next_attack_threshold = (
            max(
                float(np.percentile(positive_novelty, 80)),
                float(np.max(positive_novelty)) * 0.22,
            )
            if positive_novelty.size
            else float("inf")
        )
        end = frame
        maximum_frames = round(frame_rate * 2.4)
        minimum_attack_gap = max(1, round(frame_rate * 0.20))
        quiet_frames = 0
        while end + 1 < len(rms) and end - frame < maximum_frames:
            next_frame = end + 1
            if (
                next_frame - frame >= minimum_attack_gap
                and float(onset_envelope[next_frame]) >= next_attack_threshold
            ):
                break
            quiet_frames = quiet_frames + 1 if float(rms[next_frame]) < threshold else 0
            if quiet_frames >= 2:
                end = next_frame - quiet_frames + 1
                break
            end = next_frame
        return round((end - frame) / frame_rate * 1000)
