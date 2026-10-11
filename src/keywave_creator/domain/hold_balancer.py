"""Keep sustained notes as sparse musical accents without rearranging a chart."""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import replace

from keywave_creator.contract.models import Chart, Note, NoteType, validate_nested_charts

from .analysis import AnalysisResult

MAXIMUM_HOLD_PERCENT: int = 12


def balance_holds(charts: tuple[Chart, ...], analysis: AnalysisResult) -> tuple[Chart, ...]:
    """Thin validated nested charts, preserving IDs, attacks, lanes and input objects.

    Keep at most 12% holds per tier, with one active hold and at least one beat
    (300 ms minimum) of recovery. Rank original sustain evidence before selecting;
    apply each decision identically in every difficulty inheriting that note.
    """
    analysis.validate()
    validate_nested_charts(charts)
    beat_ms = round(60_000 / max(1, analysis.bpm))
    recovery_ms = max(300, beat_ms)
    minimum_duration_ms = max(500, min(900, beat_ms))
    first_tier_by_id: dict[str, int] = {}
    budgets = [len(chart.notes) * MAXIMUM_HOLD_PERCENT // 100 for chart in charts]
    retained_counts = [0] * len(charts)
    for tier, chart in enumerate(charts):
        for note in chart.notes:
            first_tier_by_id.setdefault(note.id, tier)

    quality_by_time = _sustain_quality(analysis)
    candidates = sorted(
        (
            note
            for note in charts[-1].notes
            if note.type is NoteType.HOLD and (note.duration_ms or 0) >= minimum_duration_ms
        ),
        key=lambda note: (-quality_by_time.get(note.time_ms, 0), note.time_ms, note.lane, note.id),
    )
    retained_ids: set[str] = set()
    retained_starts: list[int] = []
    retained_notes: list[Note] = []
    for candidate in candidates:
        inherited_tiers = range(first_tier_by_id[candidate.id], len(charts))
        if any(retained_counts[tier] >= budgets[tier] for tier in inherited_tiers):
            continue
        insertion = bisect_left(retained_starts, candidate.time_ms)
        end_ms = candidate.time_ms + (candidate.duration_ms or 0)
        if insertion > 0:
            previous = retained_notes[insertion - 1]
            if candidate.time_ms - previous.time_ms - (previous.duration_ms or 0) < recovery_ms:
                continue
        if insertion < len(retained_notes) and retained_starts[insertion] - end_ms < recovery_ms:
            continue
        retained_starts.insert(insertion, candidate.time_ms)
        retained_notes.insert(insertion, candidate)
        retained_ids.add(candidate.id)
        for tier in inherited_tiers:
            retained_counts[tier] += 1

    return tuple(
        replace(
            chart,
            notes=tuple(
                replace(note, type=NoteType.TAP, duration_ms=None)
                if note.type is NoteType.HOLD and note.id not in retained_ids
                else note
                for note in chart.notes
            ),
        )
        for chart in charts
    )


def _sustain_quality(analysis: AnalysisResult) -> dict[int, float]:
    melodic = [
        point.melodic_strength if point.melodic_strength is not None else point.strength
        for point in analysis.points
    ]
    maximum_strength = max(0.000001, max(point.strength for point in analysis.points))
    maximum_melodic = max(0.000001, max(melodic))
    return {
        point.time_ms: (
            0.45 * point.strength / maximum_strength
            + 0.35 * melodic_strength / maximum_melodic
            + 0.20 * min(1, point.sustain_ms / 2000)
        )
        for point, melodic_strength in zip(analysis.points, melodic, strict=True)
    }
