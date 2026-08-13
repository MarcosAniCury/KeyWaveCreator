"""Resolve safe, human-readable output names for generated packages."""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from os import O_CREAT, O_EXCL, O_WRONLY, close
from os import open as open_file_descriptor
from pathlib import Path

from .errors import CreatorError, CreatorErrorCode

LOGGER = logging.getLogger(__name__)
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
_RESERVATION_SUFFIX = ".keywave-creator-reservation"


@dataclass(frozen=True, slots=True)
class PackageDestinationReservation:
    """Own an atomic sidecar reservation for one future package destination."""

    destination: Path
    marker: Path

    def release(self) -> None:
        """Release the marker without touching a successfully generated package."""
        try:
            self.marker.unlink(missing_ok=True)
        except OSError as error:
            # A stale marker consumes one filename but must not invalidate a package.
            LOGGER.warning("Could not release package destination reservation: %s", error)


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


def infer_local_metadata(source: Path) -> tuple[str, str]:
    """Infer conservative display metadata from an authorized local filename."""
    normalized_stem = _metadata_value(source.stem, UNTITLED_VIDEO)
    if " - " not in normalized_stem:
        return normalized_stem, UNKNOWN_ARTIST

    artist, title = normalized_stem.split(" - ", 1)
    return (
        _metadata_value(title, UNTITLED_VIDEO),
        _metadata_value(artist, UNKNOWN_ARTIST),
    )


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


def reserve_available_package_destination(
    output_directory: Path,
    title: str,
    artist: str,
) -> PackageDestinationReservation:
    """Atomically reserve a non-existing package name for a concurrent job."""
    prepared_candidate = available_package_destination(output_directory, title, artist)
    directory = prepared_candidate.parent
    title_component = _filename_component(title, UNTITLED_VIDEO)
    artist_component = _filename_component(artist, UNKNOWN_ARTIST)
    stem = (
        title_component
        if artist_component.casefold() == UNKNOWN_ARTIST.casefold()
        else f"{artist_component} - {title_component}"
    )
    for number in range(1, _MAX_COLLISIONS + 1):
        suffix = "" if number == 1 else f" ({number})"
        candidate = directory / f"{stem}{suffix}.keywave"
        marker = candidate.with_name(candidate.name + _RESERVATION_SUFFIX)
        if candidate.exists():
            continue
        try:
            descriptor = open_file_descriptor(marker, O_CREAT | O_EXCL | O_WRONLY)
        except FileExistsError:
            continue
        except OSError as error:
            raise CreatorError(
                CreatorErrorCode.PACKAGE_FAILED,
                "The output filename could not be reserved. Choose another folder.",
                diagnostic=str(error),
            ) from error
        close(descriptor)
        if candidate.exists():
            marker.unlink(missing_ok=True)
            continue
        return PackageDestinationReservation(destination=candidate, marker=marker)
    raise CreatorError(
        CreatorErrorCode.PACKAGE_FAILED,
        "No available filename could be reserved in the output folder.",
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
