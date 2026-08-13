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


def test_creator_exposes_a_non_blocking_bounded_queue() -> None:
    qml = (
        files("keywave_creator.presentation")
        .joinpath("qml", "Main.qml")
        .read_text(encoding="utf-8")
    )

    assert "creatorController.enqueueSource" in qml
    assert "model: creatorController.queueModel" in qml
    assert "Add source to queue" in qml
    assert "Up to two levels are generated at once" in qml
    assert "enabled: !creatorController.isRunning" not in qml


def test_creator_explains_spotify_metadata_to_youtube_playlist_flow() -> None:
    qml = (
        files("keywave_creator.presentation")
        .joinpath("qml", "Main.qml")
        .read_text(encoding="utf-8")
    )

    assert "YouTube video or playlist, or a Spotify playlist" in qml
    assert "Spotify identifies tracks; YouTube provides" in qml
    assert "creatorController.spotifyClientId" in qml
    assert "http://127.0.0.1:9657/callback" in qml
