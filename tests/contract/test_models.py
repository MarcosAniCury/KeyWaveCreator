from __future__ import annotations

import pytest

from keywave_creator.contract.errors import ContractError, ErrorCode
from keywave_creator.contract.models import (
    ACTIVE_LANES,
    Chart,
    Difficulty,
    Note,
    NoteType,
    validate_nested_charts,
)


def make_charts() -> tuple[Chart, ...]:
    easy_note = Note(id="note-1", time_ms=500, lane=0)
    medium_note = Note(id="note-2", time_ms=1000, lane=3)
    hard_note = Note(id="note-3", time_ms=1500, lane=4)
    extreme_note = Note(
        id="note-4",
        time_ms=2000,
        lane=2,
        type=NoteType.HOLD,
        duration_ms=900,
    )
    notes: list[Note] = []
    charts: list[Chart] = []
    for difficulty, addition in (
        (Difficulty.EASY, easy_note),
        (Difficulty.MEDIUM, medium_note),
        (Difficulty.HARD, hard_note),
        (Difficulty.EXTREME, extreme_note),
    ):
        notes.append(addition)
        charts.append(
            Chart(
                chart_id=f"chart-{difficulty.value}",
                difficulty=difficulty,
                active_lanes=ACTIVE_LANES[difficulty],
                notes=tuple(sorted(notes)),
            )
        )
    return tuple(charts)


def test_selected_lane_profiles_are_contractual() -> None:
    assert ACTIVE_LANES[Difficulty.EASY] == (0, 1, 4, 5)
    assert ACTIVE_LANES[Difficulty.MEDIUM] == (0, 1, 3, 4, 5)
    assert ACTIVE_LANES[Difficulty.HARD] == (0, 1, 3, 4, 5)
    assert ACTIVE_LANES[Difficulty.EXTREME] == (0, 1, 2, 3, 4, 5)


def test_valid_nested_charts_round_trip() -> None:
    charts = make_charts()
    for chart in charts:
        chart.validate(duration_ms=5_000)
        assert Chart.from_dict(chart.to_dict()) == chart
    validate_nested_charts(charts)


def test_hold_requires_duration() -> None:
    note = Note(id="broken", time_ms=100, lane=0, type=NoteType.HOLD)
    with pytest.raises(ContractError) as raised:
        note.validate(duration_ms=5_000, active_lanes=(0,))
    assert raised.value.issue.code is ErrorCode.INVALID_CHART


def test_lower_difficulty_note_cannot_change() -> None:
    easy, medium, hard, extreme = make_charts()
    changed = Chart(
        chart_id=medium.chart_id,
        difficulty=medium.difficulty,
        notes=(Note(id="note-1", time_ms=501, lane=0), medium.notes[1]),
    )
    with pytest.raises(ContractError) as raised:
        validate_nested_charts((easy, changed, hard, extreme))
    assert raised.value.issue.code is ErrorCode.INVALID_CHART
