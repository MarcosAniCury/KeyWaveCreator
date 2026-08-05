from __future__ import annotations

from pathlib import Path

from keywave_creator.application.output import (
    UNKNOWN_ARTIST,
    available_package_destination,
    resolved_metadata,
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
