"""Resolve safe, human-readable output names for generated packages."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from .errors import CreatorError, CreatorErrorCode

UNKNOWN_ARTIST = "Unknown artist"
UNTITLED_VIDEO = "Untitled video"
_INVALID_WINDOWS_CHARACTERS = re.compile(r'[<>:"/\\|?*]')
_WHITESPACE = re.compile(r"\s+")
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
)
_MAX_COMPONENT_LENGTH = 80
_MAX_COLLISIONS = 10_000


def resolved_metadata(
    requested_title: str,
    requested_artist: str,
    acquired_title: str | None,
    acquired_artist: str | None,
) -> tuple[str, str]:
    """Prefer explicit metadata, then bounded acquired values, then safe fallbacks."""
    title = _metadata_value(requested_title or acquired_title, UNTITLED_VIDEO)
    artist = _metadata_value(requested_artist or acquired_artist, UNKNOWN_ARTIST)
    return title, artist


def available_package_destination(output_directory: Path, title: str, artist: str) -> Path:
    """Return a non-existing `.keywave` destination without overwriting user files."""
    directory = output_directory.expanduser().resolve()
    try:
        if directory.exists() and not directory.is_dir():
            raise CreatorError(
                CreatorErrorCode.PACKAGE_FAILED,
                "The saved output location is not a folder. Choose another folder.",
            )
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise CreatorError(
            CreatorErrorCode.PACKAGE_FAILED,
            "The output folder cannot be used. Choose another folder.",
            diagnostic=str(error),
        ) from error

    title_component = _filename_component(title, UNTITLED_VIDEO)
    artist_component = _filename_component(artist, UNKNOWN_ARTIST)
    stem = (
        title_component
        if artist_component.casefold() == UNKNOWN_ARTIST.casefold()
        else f"{artist_component} - {title_component}"
    )
    candidate = directory / f"{stem}.keywave"
    if not candidate.exists():
        return candidate
    for number in range(2, _MAX_COLLISIONS + 1):
        candidate = directory / f"{stem} ({number}).keywave"
        if not candidate.exists():
            return candidate
    raise CreatorError(
        CreatorErrorCode.PACKAGE_FAILED,
        "No available filename could be created in the output folder.",
    )


def _metadata_value(value: str | None, fallback: str) -> str:
    if value is None:
        return fallback
    normalized = _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", value)).strip()
    return normalized[:200].rstrip() or fallback


def _filename_component(value: str, fallback: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    printable = "".join(
        character for character in normalized if unicodedata.category(character)[0] != "C"
    )
    safe = _INVALID_WINDOWS_CHARACTERS.sub("-", printable)
    safe = _WHITESPACE.sub(" ", safe).strip(" .")
    safe = safe[:_MAX_COMPONENT_LENGTH].rstrip(" .") or fallback
    if safe.casefold().split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
        safe = f"_{safe}"
    return safe
