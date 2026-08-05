"""Deterministic, nested four-difficulty rhythm-chart generation."""

from __future__ import annotations

import hashlib
import uuid
from collections import deque
from dataclasses import dataclass, replace

from keywave_creator.contract.models import (
    ACTIVE_LANES,
    DIFFICULTY_ORDER,
    Chart,
    Note,
    NoteType,
    validate_nested_charts,
)

from .analysis import AnalysisResult, FeaturePoint

ALGORITHM_VERSION = "1.2.1"
HOLD_RELEASE_GAP_MS = 90
MAX_HOLD_DURATION_MS = 2_000
LANE_MEMORY_SIZE = 12


@dataclass(frozen=True, slots=True)
class GenerationOptions:
    """Control reproducible generation without changing the package contract."""

    seed: str

    def validate(self) -> None:
        if not self.seed or len(self.seed) > 128:
            raise ValueError("Seed must contain between 1 and 128 characters.")


@dataclass(frozen=True, slots=True)
class _TierRule:
    minimum_spacing_ms: int
    strength_quantile: float


_RULES: tuple[_TierRule, ...] = (
    _TierRule(minimum_spacing_ms=520, strength_quantile=0.58),
    _TierRule(minimum_spacing_ms=330, strength_quantile=0.40),
    _TierRule(minimum_spacing_ms=190, strength_quantile=0.20),
    _TierRule(minimum_spacing_ms=90, strength_quantile=0.00),
)


class _LanePlanner:
    """Translate musical contour into varied, deterministic lane movement."""

    def __init__(self, seed: str) -> None:
        self._seed = seed
        self._recent_primary_lanes: deque[int] = deque(maxlen=LANE_MEMORY_SIZE)
        self._previous_primary_lane: int | None = None
        self._previous_low_frequency_bias: float | None = None

    def order(
        self,
        lanes: tuple[int, ...],
        *,
        point: FeaturePoint,
        tier: int,
    ) -> list[int]:
        ordered_physical_lanes = sorted(lanes)
        if not ordered_physical_lanes:
            raise ValueError("At least one active lane is required.")

        target_position = self._target_position(ordered_physical_lanes, point)
        recent_counts = [0] * 6
        for lane in self._recent_primary_lanes:
            recent_counts[lane] += 1
        repeated_primary_count = self._repeated_primary_count()

        def lane_key(lane: int) -> tuple[float, bytes]:
            position = ordered_physical_lanes.index(lane)
            repeat_penalty = 0.0
            if lane == self._previous_primary_lane:
                repeat_penalty = 0.85 + (4.0 if repeated_primary_count >= 2 else 0.0)
            score = (
                abs(position - target_position) * 0.32 + recent_counts[lane] * 0.72 + repeat_penalty
            )
            value = f"{self._seed}:{point.time_ms}:{point.low_frequency_bias:.6f}:{tier}:{lane}"
            return score, hashlib.sha256(value.encode("utf-8")).digest()

        return sorted(ordered_physical_lanes, key=lane_key)

    def commit(self, primary_lane: int, point: FeaturePoint) -> None:
        self._recent_primary_lanes.append(primary_lane)
        self._previous_primary_lane = primary_lane
        self._previous_low_frequency_bias = point.low_frequency_bias

    def _target_position(
        self,
        ordered_physical_lanes: list[int],
        point: FeaturePoint,
    ) -> float:
        last_lane = self._previous_primary_lane
        last_bias = self._previous_low_frequency_bias
        if last_lane is None or last_bias is None:
            return (1.0 - point.low_frequency_bias) * (len(ordered_physical_lanes) - 1)

        previous_position = min(
            range(len(ordered_physical_lanes)),
            key=lambda position: abs(ordered_physical_lanes[position] - last_lane),
        )
        # A rising spectral centroid lowers low_frequency_bias and moves the phrase right;
        # a falling centroid moves it left. Absolute timbre must not pin a whole song to
        # one edge, so only the contour delta influences subsequent positions.
        contour_delta = (
            (last_bias - point.low_frequency_bias) * (len(ordered_physical_lanes) - 1) * 1.75
        )
        return max(
            0.0,
            min(float(len(ordered_physical_lanes) - 1), previous_position + contour_delta),
        )

    def _repeated_primary_count(self) -> int:
        previous_lane = self._previous_primary_lane
        if previous_lane is None:
            return 0
        count = 0
        for lane in reversed(self._recent_primary_lanes):
            if lane != previous_lane:
                break
            count += 1
        return count


class ChartGenerator:
    """Turn immutable features into four canonically nested charts."""

    def generate(
        self,
        analysis: AnalysisResult,
        options: GenerationOptions,
    ) -> tuple[Chart, ...]:
        analysis.validate()
        options.validate()

        selected_by_tier = self._select_nested_points(analysis.points)
        minimum_tier_by_index: dict[int, int] = {}
        for tier, selected in enumerate(selected_by_tier):
            for point_index in selected:
                minimum_tier_by_index.setdefault(point_index, tier)

        strengths = [point.strength for point in analysis.points]
        minimum_strength = min(strengths)
        strength_span = max(strengths) - minimum_strength
        lane_ends = [0] * 6
        notes_by_tier: list[list[Note]] = [[] for _ in DIFFICULTY_ORDER]
        note_context: dict[str, tuple[FeaturePoint, float, int]] = {}
        lane_planner = _LanePlanner(options.seed)
        serial = 0

        for point_index, point in enumerate(analysis.points):
            minimum_tier = minimum_tier_by_index.get(point_index)
            if minimum_tier is None:
                continue
            time_ms = point.time_ms
            if time_ms < 0 or time_ms > analysis.duration_ms:
                continue

            normalized_strength = (
                (point.strength - minimum_strength) / strength_span if strength_span else 0.5
            )
            difficulty = DIFFICULTY_ORDER[minimum_tier]
            musical_order = lane_planner.order(
                ACTIVE_LANES[difficulty],
                point=point,
                tier=minimum_tier,
            )
            musical_position = {lane: index for index, lane in enumerate(musical_order)}
            ordered_lanes = sorted(
                musical_order,
                key=lambda lane: (
                    lane_ends[lane] > time_ms,
                    lane_ends[lane] if lane_ends[lane] > time_ms else 0,
                    musical_position[lane],
                ),
            )
            chord_size = min(
                len(ordered_lanes),
                self._chord_size(minimum_tier, normalized_strength, point.is_beat),
            )
            lane_planner.commit(ordered_lanes[0], point)
            for chord_index, lane in enumerate(ordered_lanes[:chord_size]):
                note_id = f"note-{time_ms:07d}-{lane}-{serial:06d}"
                note = Note(
                    id=note_id,
                    time_ms=time_ms,
                    lane=lane,
                )
                notes_by_tier[minimum_tier].append(note)
                note_context[note_id] = (point, normalized_strength, chord_index)
                # Reserve a sustained primary lane when another lane is available. If every
                # lane is busy the layout still keeps the note and the hold is clipped later,
                # so articulation never changes density, chords, or difficulty membership.
                reservation_ms = (
                    self._hold_reservation_ms(point, normalized_strength, analysis.bpm)
                    if chord_index == 0
                    else 0
                )
                lane_ends[lane] = max(lane_ends[lane], time_ms + reservation_ms)
                serial += 1

        if not notes_by_tier[0]:
            point = max(analysis.points, key=lambda item: item.strength)
            time_ms = point.time_ms
            notes_by_tier[0].append(
                Note(id=f"note-{time_ms:07d}-0-{serial:06d}", time_ms=time_ms, lane=0)
            )

        notes_by_tier = self._apply_holds(
            notes_by_tier,
            note_context,
            bpm=analysis.bpm,
            song_duration_ms=analysis.duration_ms,
        )

        charts: list[Chart] = []
        cumulative_notes: list[Note] = []
        for tier, difficulty in enumerate(DIFFICULTY_ORDER):
            cumulative_notes.extend(notes_by_tier[tier])
            ordered_notes = tuple(
                sorted(cumulative_notes, key=lambda note: (note.time_ms, note.lane, note.id))
            )
            chart_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"keywave:{ALGORITHM_VERSION}:{options.seed}:{difficulty.value}",
                )
            )
            chart = Chart(chart_id=chart_id, difficulty=difficulty, notes=ordered_notes)
            chart.validate(duration_ms=analysis.duration_ms)
            charts.append(chart)

        result = tuple(charts)
        validate_nested_charts(result)
        return result

    @staticmethod
    def _select_nested_points(points: tuple[FeaturePoint, ...]) -> tuple[set[int], ...]:
        selections: list[set[int]] = []
        cumulative: set[int] = set()
        strengths = sorted(point.strength for point in points)
        for rule in _RULES:
            threshold_index = min(
                len(strengths) - 1,
                int((len(strengths) - 1) * rule.strength_quantile),
            )
            threshold = strengths[threshold_index]
            last_time = -rule.minimum_spacing_ms
            selected: set[int] = set()
            for index, point in enumerate(points):
                is_eligible = point.strength >= threshold or point.is_beat
                if is_eligible and point.time_ms - last_time >= rule.minimum_spacing_ms:
                    selected.add(index)
                    last_time = point.time_ms
            if not selected:
                selected.add(max(range(len(points)), key=lambda index: points[index].strength))
            cumulative |= selected
            selections.append(set(cumulative))
        return tuple(selections)

    @staticmethod
    def _chord_size(tier: int, normalized_strength: float, is_beat: bool) -> int:
        adjusted = normalized_strength + (0.05 if is_beat else 0.0)
        if tier >= 2 and adjusted >= 0.92:
            return 3
        if adjusted >= (0.88 - 0.06 * tier):
            return 2
        return 1

    @staticmethod
    def _apply_holds(
        notes_by_tier: list[list[Note]],
        note_context: dict[str, tuple[FeaturePoint, float, int]],
        *,
        bpm: float,
        song_duration_ms: int,
    ) -> list[list[Note]]:
        all_notes = sorted(
            (note for tier_notes in notes_by_tier for note in tier_notes),
            key=lambda note: (note.lane, note.time_ms, note.id),
        )
        next_time_by_id: dict[str, int | None] = {}
        notes_by_lane: list[list[Note]] = [[] for _ in range(6)]
        for note in all_notes:
            notes_by_lane[note.lane].append(note)
        for lane in range(6):
            lane_notes = notes_by_lane[lane]
            for index, note in enumerate(lane_notes):
                next_time_by_id[note.id] = (
                    lane_notes[index + 1].time_ms if index + 1 < len(lane_notes) else None
                )

        articulated: dict[str, Note] = {}
        for note in all_notes:
            context = note_context.get(note.id)
            if context is None:
                articulated[note.id] = note
                continue
            point, normalized_strength, chord_index = context
            duration_ms = ChartGenerator._hold_duration(
                point=point,
                normalized_strength=normalized_strength,
                chord_index=chord_index,
                time_ms=note.time_ms,
                next_note_time_ms=next_time_by_id[note.id],
                song_duration_ms=song_duration_ms,
                bpm=bpm,
            )
            articulated[note.id] = (
                replace(note, type=NoteType.HOLD, duration_ms=duration_ms)
                if duration_ms is not None
                else note
            )

        return [
            [articulated.get(note.id, note) for note in tier_notes] for tier_notes in notes_by_tier
        ]

    @staticmethod
    def _hold_duration(
        *,
        point: FeaturePoint,
        normalized_strength: float,
        chord_index: int,
        time_ms: int,
        next_note_time_ms: int | None,
        song_duration_ms: int,
        bpm: float,
    ) -> int | None:
        if chord_index > 0:
            return None
        beat_ms = 60_000.0 / bpm
        reservation_ms = ChartGenerator._hold_reservation_ms(
            point,
            normalized_strength,
            bpm,
        )
        if reservation_ms <= 0:
            return None

        available_ms = song_duration_ms - time_ms
        if next_note_time_ms is not None:
            available_ms = min(available_ms, next_note_time_ms - time_ms - HOLD_RELEASE_GAP_MS)
        raw_duration_ms = min(reservation_ms, available_ms)
        subdivision_ms = max(120, min(300, round(beat_ms / 2)))
        duration_ms = raw_duration_ms // subdivision_ms * subdivision_ms
        minimum_hold_ms = max(300, subdivision_ms * 2)
        if duration_ms < minimum_hold_ms:
            return None
        return duration_ms

    @staticmethod
    def _hold_reservation_ms(
        point: FeaturePoint,
        normalized_strength: float,
        bpm: float,
    ) -> int:
        beat_ms = 60_000.0 / bpm
        minimum_sustain_ms = max(360, round(beat_ms * 0.72))
        if point.sustain_ms < minimum_sustain_ms or (
            normalized_strength < 0.32 and not point.is_beat
        ):
            return 0
        return min(MAX_HOLD_DURATION_MS, point.sustain_ms)
