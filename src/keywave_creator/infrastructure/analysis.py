"""Five-millisecond multiband analysis over the canonical PCM timeline."""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

import numpy as np
import soundfile as sf
from numpy.typing import NDArray
from scipy.signal import lfilter

from keywave_creator.application.errors import CreatorError, CreatorErrorCode
from keywave_creator.application.models import CancellationToken
from keywave_creator.domain.analysis import AnalysisResult, FeaturePoint, TempoSection

SAMPLE_RATE_HZ = 48_000
ANALYSIS_SAMPLE_RATE_HZ = 12_000
DOWNSAMPLE_FACTOR = SAMPLE_RATE_HZ // ANALYSIS_SAMPLE_RATE_HZ
HOP_SIZE = 60
HOP_DURATION_MS = 5
BAND_EDGES_HZ = np.asarray((100, 260, 620, 1_400, 3_000), dtype=np.float64)
BAND_COUNT = len(BAND_EDGES_HZ) + 1
MINIMUM_ONSET_INTERVAL_SECONDS = 0.09
SILENCE_FLOOR_BELOW_PEAK_DB = -42.0
TEMPO_SECTION_MS = 30_000
MIN_BPM = 60
MAX_BPM = 200
SOURCE_BLOCK_FRAMES = SAMPLE_RATE_HZ * 5


@dataclass(frozen=True, slots=True)
class _FeatureFrames:
    rms: NDArray[np.float64]
    low_frequency_bias: NDArray[np.float64]
    flux: NDArray[np.float64]
    percussive_strength: NDArray[np.float64]
    melodic_strength: NDArray[np.float64]

    @property
    def count(self) -> int:
        return int(self.rms.size)


@dataclass(frozen=True, slots=True)
class _Peak:
    frame_index: int
    refined_frame: float
    strength: float


@dataclass(frozen=True, slots=True)
class _TempoEstimate:
    beat_frames: frozenset[int]
    representative_bpm: float
    confidence: float
    sections: tuple[TempoSection, ...]


class StreamingFeatureExtractor:
    """Find attacks, local tempo, and sustains without loading full-resolution PCM."""

    def analyze(
        self,
        audio_path: Path,
        *,
        expected_duration_ms: int,
        cancellation: CancellationToken,
    ) -> AnalysisResult:
        cancellation.raise_if_cancelled()
        try:
            duration_ms, frames = self._stream_features(audio_path, cancellation)
        except CreatorError:
            raise
        except Exception as error:
            raise CreatorError(
                CreatorErrorCode.ANALYSIS_FAILED,
                "The audio could not be analyzed.",
                diagnostic=str(error),
            ) from error

        if abs(duration_ms - expected_duration_ms) > 10:
            raise CreatorError(
                CreatorErrorCode.ANALYSIS_FAILED,
                "Decoded audio duration does not match the canonical timeline.",
            )
        if frames.count < 2 or float(np.max(frames.rms)) < 1e-6:
            raise CreatorError(
                CreatorErrorCode.ANALYSIS_FAILED,
                "No usable rhythmic events were found in the audio.",
            )

        novelty = self._build_superflux(frames.flux)
        peaks = self._find_peaks(novelty)
        if not peaks:
            fallback = int(np.argmax(novelty))
            peaks = (_Peak(fallback, float(fallback), float(novelty[fallback])),)
        tempo = self._estimate_local_tempo(peaks, frames.count, duration_ms)
        silence_floor = max(
            1e-6,
            float(np.max(frames.rms)) * 10 ** (SILENCE_FLOOR_BELOW_PEAK_DB / 20.0),
        )
        fallback_strength = float(np.median([peak.strength for peak in peaks]))
        points: list[FeaturePoint] = []
        for peak in peaks:
            cancellation.raise_if_cancelled()
            frame = peak.frame_index
            if float(frames.rms[frame]) < silence_floor:
                continue
            time_ms = round((peak.refined_frame + 0.5) * HOP_DURATION_MS)
            if not 0 <= time_ms <= expected_duration_ms:
                continue
            points.append(
                FeaturePoint(
                    time_ms=time_ms,
                    strength=max(peak.strength, fallback_strength * 0.7),
                    low_frequency_bias=float(frames.low_frequency_bias[frame]),
                    sustain_ms=self._estimate_sustain_ms(
                        frames,
                        novelty,
                        frame,
                        silence_floor,
                    ),
                    is_beat=frame in tempo.beat_frames,
                    percussive_strength=float(frames.percussive_strength[frame]),
                    melodic_strength=float(frames.melodic_strength[frame]),
                )
            )
        if not points:
            raise CreatorError(
                CreatorErrorCode.ANALYSIS_FAILED,
                "No usable rhythmic events were found in the audio.",
            )
        confidence = float(
            np.clip(
                0.28
                + 0.32 * min(1.0, len(points) / 96)
                + 0.20 * min(1.0, len(tempo.beat_frames) / 24)
                + 0.20 * tempo.confidence,
                0,
                1,
            )
        )
        result = AnalysisResult(
            duration_ms=expected_duration_ms,
            bpm=tempo.representative_bpm,
            points=tuple(points),
            confidence=round(confidence, 4),
            tempo_sections=tempo.sections,
        )
        result.validate()
        return result

    @staticmethod
    def _stream_features(
        audio_path: Path,
        cancellation: CancellationToken,
    ) -> tuple[int, _FeatureFrames]:
        rms_values: list[NDArray[np.float64]] = []
        bias_values: list[NDArray[np.float64]] = []
        flux_values: list[NDArray[np.float64]] = []
        percussive_values: list[NDArray[np.float64]] = []
        melodic_values: list[NDArray[np.float64]] = []
        coefficients = 1 - np.exp(-2 * np.pi * BAND_EDGES_HZ / ANALYSIS_SAMPLE_RATE_HZ)
        filter_states = np.zeros(BAND_EDGES_HZ.size, dtype=np.float64)
        previous_log_history = np.zeros((6, BAND_COUNT), dtype=np.float64)

        with sf.SoundFile(audio_path) as audio:
            sample_rate = int(audio.samplerate)
            duration_ms = round(len(audio) / sample_rate * 1_000)
            if sample_rate != SAMPLE_RATE_HZ:
                raise CreatorError(
                    CreatorErrorCode.ANALYSIS_FAILED,
                    "Canonical audio must use a 48 kHz sample rate.",
                )
            while True:
                cancellation.raise_if_cancelled()
                block_value = audio.read(
                    SOURCE_BLOCK_FRAMES,
                    dtype="float32",
                    always_2d=True,
                )
                if block_value.size == 0:
                    break
                mono = np.mean(np.asarray(block_value, dtype=np.float64), axis=1)
                complete_source = mono.size // DOWNSAMPLE_FACTOR * DOWNSAMPLE_FACTOR
                if complete_source == 0:
                    continue
                downsampled = np.mean(
                    mono[:complete_source].reshape(-1, DOWNSAMPLE_FACTOR),
                    axis=1,
                )
                complete_analysis = downsampled.size // HOP_SIZE * HOP_SIZE
                if complete_analysis == 0:
                    continue
                downsampled = downsampled[:complete_analysis]
                low_passes: list[NDArray[np.float64]] = []
                for index, coefficient in enumerate(coefficients):
                    filtered, final_state = lfilter(
                        (float(coefficient),),
                        (1.0, -(1.0 - float(coefficient))),
                        downsampled,
                        zi=(float(filter_states[index]),),
                    )
                    filter_states[index] = float(final_state[0])
                    low_passes.append(np.asarray(filtered, dtype=np.float64))

                band_samples: list[NDArray[np.float64]] = []
                previous_low = np.zeros_like(downsampled)
                for low_pass in low_passes:
                    band_samples.append(low_pass - previous_low)
                    previous_low = low_pass
                band_samples.append(downsampled - previous_low)
                band_energy = np.stack(
                    [
                        np.sum(np.square(samples.reshape(-1, HOP_SIZE)), axis=1)
                        for samples in band_samples
                    ],
                    axis=1,
                )
                full_energy = np.sum(
                    np.square(downsampled.reshape(-1, HOP_SIZE)),
                    axis=1,
                )
                log_energy = np.log1p(band_energy / HOP_SIZE * 100_000)
                temporal_context = np.vstack((previous_log_history, log_energy))
                row_count = log_energy.shape[0]
                history_count = previous_log_history.shape[0]
                previous_maximum = np.full_like(log_energy, -math.inf)
                for lag in range(1, history_count + 1):
                    previous_maximum = np.maximum(
                        previous_maximum,
                        temporal_context[history_count - lag : history_count - lag + row_count],
                    )
                previous_maximum[:, 1:] = np.maximum(
                    previous_maximum[:, 1:], previous_maximum[:, :-1]
                )
                previous_maximum[:, :-1] = np.maximum(
                    previous_maximum[:, :-1], previous_maximum[:, 1:]
                )
                increase = np.maximum(0.0, log_energy - previous_maximum)
                flux_values.append(np.sum(increase, axis=1))
                percussive_weights = np.asarray((0.75, 0.75, 0.75, 1.25, 1.25, 1.25))
                melodic_weights = np.asarray((0.35, 1.0, 1.0, 1.0, 1.0, 0.35))
                percussive_values.append(np.sum(increase * percussive_weights, axis=1))
                melodic_values.append(np.sum(log_energy * melodic_weights, axis=1) / BAND_COUNT)
                total = np.sum(band_energy, axis=1)
                low = band_energy[:, 0] + band_energy[:, 1]
                bias_values.append(np.divide(low, total, out=np.zeros_like(low), where=total > 0))
                rms_values.append(np.sqrt(full_energy / HOP_SIZE))
                previous_log_history = temporal_context[-history_count:]

        def combined(values: list[NDArray[np.float64]]) -> NDArray[np.float64]:
            return np.concatenate(values) if values else np.empty(0, dtype=np.float64)

        return duration_ms, _FeatureFrames(
            rms=combined(rms_values),
            low_frequency_bias=combined(bias_values),
            flux=combined(flux_values),
            percussive_strength=combined(percussive_values),
            melodic_strength=combined(melodic_values),
        )

    @staticmethod
    def _build_superflux(flux: NDArray[np.float64]) -> NDArray[np.float64]:
        positive = flux[flux > 0]
        scale = float(np.percentile(positive, 90)) if positive.size else 0.0
        novelty = np.zeros_like(flux) if scale <= 0 else np.clip(flux / scale, 0.0, 4.0)
        if novelty.size < 3:
            return novelty
        return np.convolve(novelty, np.asarray((0.20, 0.65, 0.15)), mode="same")

    @staticmethod
    def _find_peaks(envelope: NDArray[np.float64]) -> tuple[_Peak, ...]:
        if envelope.size < 3:
            return ()
        minimum_distance = max(
            1,
            round(MINIMUM_ONSET_INTERVAL_SECONDS * 1_000 / HOP_DURATION_MS),
        )
        radius = 40
        prefix = np.concatenate((np.zeros(1), np.cumsum(envelope)))
        peaks: list[_Peak] = []
        for index in range(1, envelope.size - 1):
            start = max(0, index - radius)
            end = min(envelope.size, index + radius + 1)
            local_mean = float((prefix[end] - prefix[start]) / (end - start))
            threshold = max(0.08, local_mean * 1.35)
            if (
                envelope[index] < threshold
                or envelope[index] < envelope[index - 1]
                or envelope[index] <= envelope[index + 1]
            ):
                continue
            divisor = envelope[index - 1] - 2 * envelope[index] + envelope[index + 1]
            adjustment = (
                0.0
                if abs(float(divisor)) < 1e-7
                else float(
                    np.clip(
                        0.5 * (envelope[index - 1] - envelope[index + 1]) / divisor,
                        -0.5,
                        0.5,
                    )
                )
            )
            candidate = _Peak(index, index + adjustment, float(envelope[index]))
            if not peaks or index - peaks[-1].frame_index >= minimum_distance:
                peaks.append(candidate)
            elif candidate.strength > peaks[-1].strength:
                peaks[-1] = candidate
        return tuple(peaks)

    @classmethod
    def _estimate_local_tempo(
        cls,
        peaks: tuple[_Peak, ...],
        frame_count: int,
        duration_ms: int,
    ) -> _TempoEstimate:
        section_frames = TEMPO_SECTION_MS // HOP_DURATION_MS
        sections: list[TempoSection] = []
        beat_frames: set[int] = set()
        bpms: list[float] = []
        confidences: list[float] = []
        for section_start in range(0, frame_count, section_frames):
            section_end = min(frame_count, section_start + section_frames)
            local_peaks = tuple(
                peak for peak in peaks if section_start <= peak.frame_index < section_end
            )
            bpm, confidence, local_beats = cls._estimate_tempo_section(local_peaks)
            beat_frames.update(local_beats)
            start_ms = section_start * HOP_DURATION_MS
            end_ms = min(duration_ms, section_end * HOP_DURATION_MS)
            if end_ms > start_ms:
                sections.append(TempoSection(start_ms, end_ms, bpm, confidence))
            if local_peaks:
                bpms.append(bpm)
                confidences.append(confidence)
        return _TempoEstimate(
            beat_frames=frozenset(beat_frames),
            representative_bpm=float(np.median(bpms)) if bpms else 120.0,
            confidence=float(np.mean(confidences)) if confidences else 0.0,
            sections=tuple(sections),
        )

    @staticmethod
    def _estimate_tempo_section(
        peaks: tuple[_Peak, ...],
    ) -> tuple[float, float, frozenset[int]]:
        if len(peaks) < 2:
            return 120.0, 0.0, frozenset(peak.frame_index for peak in peaks)
        best_bpm = 120.0
        best_score = -math.inf
        for bpm in range(MIN_BPM, MAX_BPM + 1):
            interval_frames = 60_000 / bpm / HOP_DURATION_MS
            score = 0.0
            for previous, current in pairwise(peaks):
                distance = current.refined_frame - previous.refined_frame
                multiple = max(1, round(distance / interval_frames))
                error = abs(distance - multiple * interval_frames) / interval_frames
                score += current.strength * math.exp(-(error**2) * 18) / multiple
            if score > best_score:
                best_score = score
                best_bpm = float(bpm)

        beat_interval = 60_000 / best_bpm / HOP_DURATION_MS
        path_scores = np.asarray([peak.strength for peak in peaks], dtype=np.float64)
        previous_indices = np.full(len(peaks), -1, dtype=np.int64)
        best_end = 0
        for index, peak in enumerate(peaks):
            for candidate in range(max(0, index - 12), index):
                distance = peak.refined_frame - peaks[candidate].refined_frame
                multiple = max(1, round(distance / beat_interval))
                error = abs(distance - multiple * beat_interval) / beat_interval
                transition = math.exp(-(error**2) * 32) / math.sqrt(multiple)
                score = float(path_scores[candidate]) + peak.strength * transition
                if score > path_scores[index]:
                    path_scores[index] = score
                    previous_indices[index] = candidate
            if path_scores[index] > path_scores[best_end]:
                best_end = index
        beats: set[int] = set()
        cursor = best_end
        while cursor >= 0:
            beats.add(peaks[cursor].frame_index)
            cursor = int(previous_indices[cursor])
        total_strength = sum(peak.strength for peak in peaks)
        confidence = (
            0.0
            if total_strength <= 0
            else float(np.clip(path_scores[best_end] / total_strength, 0.0, 1.0))
        )
        return best_bpm, confidence, frozenset(beats)

    @staticmethod
    def _estimate_sustain_ms(
        frames: _FeatureFrames,
        onset_envelope: NDArray[np.float64],
        frame_index: int,
        silence_floor: float,
    ) -> int:
        baseline = float(frames.melodic_strength[frame_index])
        if baseline <= 0:
            return 0
        energy_threshold = max(silence_floor, float(frames.rms[frame_index]) * 0.38)
        maximum_frames = 2_400 // HOP_DURATION_MS
        minimum_attack_gap = 180 // HOP_DURATION_MS
        end = frame_index
        quiet_frames = 0
        for next_frame in range(frame_index + 1, min(frames.count, frame_index + maximum_frames)):
            if (
                next_frame - frame_index >= minimum_attack_gap
                and onset_envelope[next_frame] >= 1.15
            ):
                break
            sustained = (
                frames.rms[next_frame] >= energy_threshold
                and frames.melodic_strength[next_frame] >= baseline * 0.30
            )
            quiet_frames = 0 if sustained else quiet_frames + 1
            if quiet_frames >= 4:
                break
            end = next_frame
        return (end - frame_index) * HOP_DURATION_MS
