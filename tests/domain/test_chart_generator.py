from __future__ import annotations

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
    assert [len(chart.notes) for chart in first] == sorted(len(chart.notes) for chart in first)
    previous_notes = set()
    for chart in first:
        assert {note.lane for note in chart.notes} <= set(ACTIVE_LANES[chart.difficulty])
        assert previous_notes <= set(chart.notes)
        previous_notes = set(chart.notes)


def test_generation_can_create_chords_and_holds_without_lane_overlap() -> None:
    charts = ChartGenerator().generate(make_analysis(), GenerationOptions(seed="holds-and-chords"))
    extreme = charts[-1]

    assert any(note.type is NoteType.HOLD for note in extreme.notes)
    assert any(
        first.time_ms == second.time_ms and first.lane != second.lane
        for first, second in zip(extreme.notes, extreme.notes[1:], strict=False)
    )
    for lane in range(6):
        lane_notes = [note for note in extreme.notes if note.lane == lane]
        for first, second in pairwise(lane_notes):
            assert first.time_ms + (first.duration_ms or 0) <= second.time_ms
