"""Deterministic, nested four-difficulty rhythm-chart generation."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass

from keywave_creator.contract.models import (
    ACTIVE_LANES,
    DIFFICULTY_ORDER,
    Chart,
    Note,
    NoteType,
    validate_nested_charts,
)

from .analysis import AnalysisResult, FeaturePoint

ALGORITHM_VERSION = "1.1.0"


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
            available_lanes = [
                lane for lane in ACTIVE_LANES[difficulty] if lane_ends[lane] <= time_ms
            ]
            if not available_lanes:
                continue
            ordered_lanes = self._ordered_lanes(
                available_lanes,
                seed=options.seed,
                point=point,
                tier=minimum_tier,
            )
            chord_size = min(
                len(ordered_lanes),
                self._chord_size(minimum_tier, normalized_strength, point.is_beat),
            )
            for chord_index, lane in enumerate(ordered_lanes[:chord_size]):
                duration_ms = self._hold_duration(
                    point=point,
                    normalized_strength=normalized_strength,
                    chord_index=chord_index,
                    time_ms=time_ms,
                    song_duration_ms=analysis.duration_ms,
                    seed=options.seed,
                )
                note_type = NoteType.HOLD if duration_ms is not None else NoteType.TAP
                note = Note(
                    id=f"note-{time_ms:07d}-{lane}-{serial:06d}",
                    time_ms=time_ms,
                    lane=lane,
                    type=note_type,
                    duration_ms=duration_ms,
                )
                notes_by_tier[minimum_tier].append(note)
                lane_ends[lane] = time_ms + (duration_ms or 0)
                serial += 1

        if not notes_by_tier[0]:
            point = max(analysis.points, key=lambda item: item.strength)
            time_ms = point.time_ms
            notes_by_tier[0].append(
                Note(id=f"note-{time_ms:07d}-0-{serial:06d}", time_ms=time_ms, lane=0)
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
    def _ordered_lanes(
        lanes: list[int],
        *,
        seed: str,
        point: FeaturePoint,
        tier: int,
    ) -> list[int]:
        def lane_key(lane: int) -> bytes:
            value = f"{seed}:{point.time_ms}:{point.low_frequency_bias:.6f}:{tier}:{lane}"
            return hashlib.sha256(value.encode("utf-8")).digest()

        return sorted(lanes, key=lane_key)

    @staticmethod
    def _chord_size(tier: int, normalized_strength: float, is_beat: bool) -> int:
        adjusted = normalized_strength + (0.05 if is_beat else 0.0)
        if tier >= 2 and adjusted >= 0.92:
            return 3
        if adjusted >= (0.88 - 0.06 * tier):
            return 2
        return 1

    @staticmethod
    def _hold_duration(
        *,
        point: FeaturePoint,
        normalized_strength: float,
        chord_index: int,
        time_ms: int,
        song_duration_ms: int,
        seed: str,
    ) -> int | None:
        if chord_index > 0 or point.sustain_ms < 420 or normalized_strength < 0.48:
            return None
        discriminator = hashlib.sha256(f"{seed}:{point.time_ms}:hold".encode()).digest()[0]
        if discriminator >= 96:
            return None
        duration_ms = min(1_600, max(300, point.sustain_ms), song_duration_ms - time_ms)
        return duration_ms if duration_ms >= 250 else None
