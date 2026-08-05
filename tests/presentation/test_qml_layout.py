from __future__ import annotations

from importlib.resources import files


def test_creator_uses_a_fixed_responsive_viewport_without_scrolling() -> None:
    qml = (
        files("keywave_creator.presentation")
        .joinpath("qml", "Main.qml")
        .read_text(encoding="utf-8")
    )

    assert "Flickable {" not in qml
    assert "ScrollBar.vertical" not in qml
    assert "readonly property real fitScale" in qml
    assert "scale: fitScale" in qml


def test_pipeline_steps_have_distinct_active_and_completed_visual_states() -> None:
    qml = (
        files("keywave_creator.presentation")
        .joinpath("qml", "Main.qml")
        .read_text(encoding="utf-8")
    )

    assert "component PipelineStep" in qml
    assert "creatorController.stage" in qml
    assert "stepCompleted" in qml
    assert "stepActive" in qml
    assert "stepIndex: modelData.index" in qml
    for color in ("#39E6D0", "#8C6CFF", "#FF5BC8", "#FFC857"):
        assert color in qml
