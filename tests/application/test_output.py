from __future__ import annotations

from pathlib import Path

from keywave_creator.application.output import (
    UNKNOWN_ARTIST,
    available_package_destination,
    infer_local_metadata,
    reserve_available_package_destination,
    resolved_metadata,
)


def test_local_metadata_uses_artist_title_filename_convention() -> None:
    assert infer_local_metadata(Path("Aurora - Running With The Wolves.flac")) == (
        "Running With The Wolves",
        "Aurora",
    )
    assert infer_local_metadata(Path("instrumental.wav")) == (
        "instrumental",
        UNKNOWN_ARTIST,
    )


def test_resolved_metadata_prefers_explicit_values_and_has_fallbacks() -> None:
    assert resolved_metadata("Override", "Artist", "Downloaded", "Channel") == (
        "Override",
        "Artist",
    )
    assert resolved_metadata("", "", None, None) == ("Untitled video", UNKNOWN_ARTIST)


def test_available_destination_handles_windows_names_and_unknown_artist(tmp_path: Path) -> None:
    destination = available_package_destination(tmp_path / "levels", "CON", UNKNOWN_ARTIST)

    assert destination == (tmp_path / "levels" / "_CON.keywave").resolve()


def test_available_destination_never_overwrites_existing_file(tmp_path: Path) -> None:
    first = available_package_destination(tmp_path, "Song", "Artist")
    first.write_bytes(b"keep")

    second = available_package_destination(tmp_path, "Song", "Artist")

    assert second.name == "Artist - Song (2).keywave"
    assert first.read_bytes() == b"keep"


def test_concurrent_destinations_are_reserved_without_collision(tmp_path: Path) -> None:
    first = reserve_available_package_destination(tmp_path, "Song", "Artist")
    second = reserve_available_package_destination(tmp_path, "Song", "Artist")

    try:
        assert first.destination.name == "Artist - Song.keywave"
        assert second.destination.name == "Artist - Song (2).keywave"
        assert first.marker.is_file()
        assert second.marker.is_file()
    finally:
        first.release()
        second.release()

    assert not first.marker.exists()
    assert not second.marker.exists()
