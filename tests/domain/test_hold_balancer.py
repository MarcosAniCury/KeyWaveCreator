from __future__ import annotations

from dataclasses import replace
from itertools import pairwise

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from keywave_creator.contract.models import (
    DIFFICULTY_ORDER,
    Chart,
    Note,
    NoteType,
    validate_nested_charts,
)
from keywave_creator.domain import chart_generator
from keywave_creator.domain.analysis import AnalysisResult, FeaturePoint
from keywave_creator.domain.chart_generator import (
    ALGORITHM_VERSION,
    ChartGenerator,
    GenerationOptions,
)
from keywave_creator.domain.hold_balancer import balance_holds


def dense_input(bpm: float = 120) -> tuple[tuple[Chart, ...], AnalysisResult]:
    notes = tuple(
        Note(index * 250, (0, 1, 4, 5)[index % 4], f"n{index}", NoteType.HOLD, 1000)
        for index in range(80)
    )
    charts = tuple(
        Chart(
            str(tier),
            difficulty,
            tuple(note for index, note in enumerate(notes) if index % 4 <= tier),
        )
        for tier, difficulty in enumerate(DIFFICULTY_ORDER)
    )
    analysis = AnalysisResult(
        duration_ms=21_000,
        bpm=bpm,
        confidence=1,
        points=tuple(FeaturePoint(note.time_ms, 0.8, 0.5, 1500) for note in notes),
    )
    return charts, analysis


def assert_balanced(charts: tuple[Chart, ...], analysis: AnalysisResult) -> None:
    validate_nested_charts(charts)
    for chart in charts:
        chart.validate(duration_ms=analysis.duration_ms)
        holds = [note for note in chart.notes if note.type is NoteType.HOLD]
        assert len(holds) <= len(chart.notes) * 12 // 100
        for first, second in pairwise(holds):
            assert second.time_ms - first.time_ms - (first.duration_ms or 0) >= max(
                300, round(60_000 / max(1, analysis.bpm))
            )
        assert all(note.duration_ms is None for note in chart.notes if note.type is NoteType.TAP)


@pytest.mark.parametrize("bpm", [60, 80, 120, 180, 240])
def test_dense_holds_are_sparse_separated_and_immutable_without_losing_attacks(bpm: float) -> None:
    charts, analysis = dense_input(bpm)
    result = balance_holds(charts, analysis)
    assert_balanced(result, analysis)
    for original, balanced in zip(charts, result, strict=True):
        assert any(note.type is NoteType.HOLD for note in balanced.notes)
        assert all(note.type is NoteType.HOLD for note in original.notes)
        assert [(n.id, n.time_ms, n.lane) for n in balanced.notes] == [
            (n.id, n.time_ms, n.lane) for n in original.notes
        ]
    assert balance_holds(charts, analysis) == result
    assert balance_holds(result, analysis) == result


def test_stronger_sustain_wins_over_earlier_overlapping_hold() -> None:
    charts, analysis = dense_input()
    analysis = replace(
        analysis,
        points=tuple(
            replace(
                point, strength=1 if index == 1 else 0.1, melodic_strength=1 if index == 1 else 0.1
            )
            for index, point in enumerate(analysis.points)
        ),
    )
    result = balance_holds(charts, analysis)
    assert result[-1].notes[0].type is NoteType.TAP
    assert result[-1].notes[1].type is NoteType.HOLD
    assert_balanced(result, analysis)


@pytest.mark.parametrize("count,expected", [(0, 0), (1, 0), (8, 0), (9, 1), (25, 3)])
def test_each_inherited_tier_respects_integer_budget(count: int, expected: int) -> None:
    notes = tuple(Note(index * 2000, 0, f"n{index}", NoteType.HOLD, 1000) for index in range(count))
    charts = tuple(
        Chart(str(tier), difficulty, notes) for tier, difficulty in enumerate(DIFFICULTY_ORDER)
    )
    analysis = AnalysisResult(max(1000, count * 2000), 120, (FeaturePoint(0, 1, 0.5, 1000),), 1)
    result = balance_holds(charts, analysis)
    assert_balanced(result, analysis)
    assert all(sum(n.type is NoteType.HOLD for n in chart.notes) == expected for chart in result)


def test_short_sustains_and_existing_taps_stay_taps() -> None:
    charts, analysis = dense_input()
    charts = tuple(
        replace(chart, notes=tuple(replace(note, duration_ms=250) for note in chart.notes))
        for chart in charts
    )
    result = balance_holds(charts, analysis)
    assert all(note.type is NoteType.TAP for chart in result for note in chart.notes)
    assert balance_holds(result, analysis) == result


@pytest.mark.parametrize("gap_ms,expected_holds", [(499, 1), (500, 2), (501, 2)])
def test_recovery_includes_the_exact_beat_boundary(gap_ms: int, expected_holds: int) -> None:
    notes = (
        Note(0, 0, "first", NoteType.HOLD, 1000),
        Note(1000 + gap_ms, 1, "second", NoteType.HOLD, 1000),
        *(Note(4000 + index * 250, 0, f"tap{index}") for index in range(23)),
    )
    charts = tuple(
        Chart(str(tier), difficulty, notes) for tier, difficulty in enumerate(DIFFICULTY_ORDER)
    )
    analysis = AnalysisResult(10_000, 120, (FeaturePoint(0, 1, 0.5, 1000),), 1)
    result = balance_holds(charts, analysis)
    assert_balanced(result, analysis)
    assert sum(note.type is NoteType.HOLD for note in result[-1].notes) == expected_holds


def test_all_six_simultaneous_holds_become_one_hold_and_five_taps() -> None:
    notes = (
        *(Note(0, lane, f"hold{lane}", NoteType.HOLD, 1000) for lane in range(6)),
        *(Note(2000 + index * 250, 0, f"tap{index}") for index in range(44)),
    )
    charts = tuple(
        Chart(str(tier), difficulty, notes if tier == 3 else ())
        for tier, difficulty in enumerate(DIFFICULTY_ORDER)
    )
    analysis = AnalysisResult(15_000, 120, (FeaturePoint(0, 1, 0.5, 1000),), 1)
    result = balance_holds(charts, analysis)
    assert_balanced(result, analysis)
    assert sum(note.type is NoteType.HOLD for note in result[-1].notes) == 1
    assert len([note for note in result[-1].notes if note.time_ms == 0]) == 6


def test_balancing_does_not_change_the_winning_arrangement(monkeypatch: pytest.MonkeyPatch) -> None:
    _, analysis = dense_input()
    options = GenerationOptions(seed="unchanged-arrangement")
    with monkeypatch.context() as patch:
        patch.setattr(chart_generator, "balance_holds", lambda charts, analysis: charts)
        original = ChartGenerator().generate(analysis, options)
    balanced = ChartGenerator().generate(analysis, options)
    assert any(note.type is NoteType.HOLD for note in balanced[-1].notes)
    assert sum(note.type is NoteType.HOLD for note in original[-1].notes) > sum(
        note.type is NoteType.HOLD for note in balanced[-1].notes
    )
    for before, after in zip(original, balanced, strict=True):
        assert before.chart_id == after.chart_id
        assert before.active_lanes == after.active_lanes
        assert [(n.id, n.time_ms, n.lane) for n in before.notes] == [
            (n.id, n.time_ms, n.lane) for n in after.notes
        ]
        for old, new in zip(before.notes, after.notes, strict=True):
            if new.type is NoteType.HOLD:
                assert new == old
    assert_balanced(balanced, analysis)


@settings(max_examples=20, deadline=None)
@given(bpm=st.integers(60, 240), sustain_ms=st.integers(0, 2000))
def test_generated_charts_keep_nested_contract_and_hold_limits(bpm: int, sustain_ms: int) -> None:
    _, analysis = dense_input(bpm)
    analysis = replace(
        analysis, points=tuple(replace(p, sustain_ms=sustain_ms) for p in analysis.points)
    )
    result = ChartGenerator().generate(analysis, GenerationOptions(seed="hold-property"))
    assert_balanced(result, analysis)
    assert ALGORITHM_VERSION == "creator-autosync-2.1.0"
