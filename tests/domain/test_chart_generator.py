from __future__ import annotations

from collections import Counter
from dataclasses import replace
from itertools import pairwise

from keywave_creator.contract.models import ACTIVE_LANES, DIFFICULTY_ORDER, NoteType
from keywave_creator.domain.analysis import AnalysisResult, FeaturePoint
from keywave_creator.domain.chart_generator import ChartGenerator, GenerationOptions


def make_analysis() -> AnalysisResult:
    points = tuple(
        FeaturePoint(
            time_ms=500 + index * 250,
            strength=1.0 + (index % 7),
            low_frequency_bias=(index % 5) / 4,
            sustain_ms=700 if index % 5 == 0 else 80,
            is_beat=index % 2 == 0,
        )
        for index in range(32)
    )
    return AnalysisResult(duration_ms=10_000, bpm=120.0, points=points, confidence=0.9)


def test_generation_is_deterministic_nested_and_uses_allowed_lanes() -> None:
    generator = ChartGenerator()
    options = GenerationOptions(seed="domain-test")

    first = generator.generate(make_analysis(), options)
    second = generator.generate(make_analysis(), options)

    assert first == second
    assert tuple(chart.difficulty for chart in first) == DIFFICULTY_ORDER
    assert [len(chart.notes) for chart in first] == [13, 26, 38, 40]
    assert [len(chart.notes) for chart in first] == sorted(len(chart.notes) for chart in first)
    previous_notes = set()
    for chart in first:
        assert {note.lane for note in chart.notes} <= set(ACTIVE_LANES[chart.difficulty])
        assert previous_notes <= set(chart.notes)
        previous_notes = set(chart.notes)


def test_generation_can_create_chords_and_holds_without_lane_overlap() -> None:
    charts = ChartGenerator().generate(make_analysis(), GenerationOptions(seed="holds-and-chords"))
    extreme = charts[-1]

    assert sum(note.type is NoteType.HOLD for note in extreme.notes) >= 4
    assert any(
        first.time_ms == second.time_ms and first.lane != second.lane
        for first, second in zip(extreme.notes, extreme.notes[1:], strict=False)
    )
    for lane in range(6):
        lane_notes = [note for note in extreme.notes if note.lane == lane]
        for first, second in pairwise(lane_notes):
            assert first.time_ms + (first.duration_ms or 0) <= second.time_ms


def test_hold_articulation_does_not_change_difficulty_density_or_note_starts() -> None:
    analysis = make_analysis()
    without_sustains = replace(
        analysis,
        points=tuple(replace(point, sustain_ms=0) for point in analysis.points),
    )
    options = GenerationOptions(seed="same-difficulty")

    sustained_charts = ChartGenerator().generate(analysis, options)
    tap_charts = ChartGenerator().generate(without_sustains, options)

    for sustained, taps in zip(sustained_charts, tap_charts, strict=True):
        assert sustained.active_lanes == taps.active_lanes
        assert [note.time_ms for note in sustained.notes] == [note.time_ms for note in taps.notes]


def test_lane_planning_follows_relative_spectral_contour() -> None:
    analysis = AnalysisResult(
        duration_ms=3_000,
        bpm=120.0,
        confidence=0.9,
        points=(
            FeaturePoint(500, 1.0, 1.0, 0),
            FeaturePoint(1_200, 1.0, 1.0, 0),
            FeaturePoint(1_900, 1.0, 0.0, 0),
        ),
    )

    easy = ChartGenerator().generate(analysis, GenerationOptions(seed="contour"))[0]
    primary_lanes = [
        min(note.lane for note in easy.notes if note.time_ms == time_ms)
        for time_ms in (500, 1_200, 1_900)
    ]

    assert primary_lanes[0] == 0
    assert primary_lanes[2] > primary_lanes[1]


def test_constant_timbre_does_not_collapse_a_chart_onto_two_lanes() -> None:
    analysis = AnalysisResult(
        duration_ms=30_000,
        bpm=120.0,
        confidence=0.9,
        points=tuple(
            FeaturePoint(
                time_ms=250 + index * 150,
                strength=1.0 + (index % 10),
                low_frequency_bias=0.92,
                sustain_ms=0,
                is_beat=index % 4 == 0,
            )
            for index in range(180)
        ),
    )

    charts = ChartGenerator().generate(analysis, GenerationOptions(seed="balanced-phrase"))

    for chart in charts:
        lane_counts = Counter(note.lane for note in chart.notes)
        assert set(lane_counts) == set(ACTIVE_LANES[chart.difficulty])
        assert max(lane_counts.values()) / len(chart.notes) < 0.4
