"""Expand supported playlist URLs into the existing single-video pipeline."""

from __future__ import annotations

import re
from collections.abc import Callable
from urllib.parse import parse_qs, urlparse

from .errors import CreatorError, CreatorErrorCode
from .models import (
    CancellationToken,
    InputSourceKind,
    ResolvedPublicVideo,
    SourceExpansionProgress,
    SourceExpansionResult,
)
from .ports import SpotifyPlaylistResolver, YouTubeMusicVideoFinder, YouTubePlaylistResolver

ResolvedVideoSink = Callable[[ResolvedPublicVideo], None]
ExpansionProgressSink = Callable[[SourceExpansionProgress], None]

_YOUTUBE_HOSTS = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtu.be",
        "www.youtu.be",
    }
)
_SPOTIFY_HOSTS = frozenset({"open.spotify.com", "www.open.spotify.com"})
_SPOTIFY_CLIENT_ID = re.compile(r"^[A-Za-z0-9]{16,64}$")


def validate_spotify_client_id(client_id: str) -> str:
    """Validate the non-secret identifier configured for Spotify PKCE."""
    value = client_id.strip()
    if not _SPOTIFY_CLIENT_ID.fullmatch(value):
        raise CreatorError(
            CreatorErrorCode.SPOTIFY_AUTH_REQUIRED,
            "Enter the Client ID from your Spotify developer app.",
        )
    return value


def classify_source_url(source: str) -> InputSourceKind:
    """Classify one HTTPS URL without performing network work."""
    value = source.strip()
    if not value:
        raise CreatorError(CreatorErrorCode.INVALID_SOURCE, "Choose a media source.")
    if len(value) > 2_048:
        raise CreatorError(CreatorErrorCode.INVALID_SOURCE, "Enter a supported source URL.")
    parsed = urlparse(value)
    host = (parsed.hostname or "").casefold()
    try:
        port = parsed.port
    except ValueError:
        port = -1
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        raise CreatorError(
            CreatorErrorCode.INVALID_SOURCE,
            "Enter an HTTPS YouTube video or playlist, or a Spotify playlist.",
        )

    if host in _YOUTUBE_HOSTS:
        query = parse_qs(parsed.query)
        video_id = query.get("v", [""])[0]
        playlist_id = query.get("list", [""])[0]
        # YouTube appends an RD-prefixed Mix/Radio context to shared watch URLs.
        # The explicit `v` remains the user's requested video; expanding the
        # generated radio would enqueue unrelated recommendations instead.
        is_radio_context = bool(video_id) and playlist_id.upper().startswith("RD")
        if playlist_id and not is_radio_context:
            return InputSourceKind.YOUTUBE_PLAYLIST
        return InputSourceKind.YOUTUBE_VIDEO

    if host in _SPOTIFY_HOSTS:
        parts = tuple(part for part in parsed.path.split("/") if part)
        if len(parts) == 2 and parts[0].casefold() == "playlist":
            return InputSourceKind.SPOTIFY_PLAYLIST

    raise CreatorError(
        CreatorErrorCode.INVALID_SOURCE,
        "Enter a YouTube video or playlist, or a Spotify playlist URL.",
    )


class PlaylistSourceExpander:
    """Resolve playlists and stream each usable video to the generation queue."""

    def __init__(
        self,
        *,
        youtube_playlists: YouTubePlaylistResolver,
        spotify_playlists: SpotifyPlaylistResolver,
        youtube_finder: YouTubeMusicVideoFinder,
    ) -> None:
        self._youtube_playlists = youtube_playlists
        self._spotify_playlists = spotify_playlists
        self._youtube_finder = youtube_finder

    def expand(
        self,
        source: str,
        *,
        spotify_client_id: str,
        resolved: ResolvedVideoSink,
        progress: ExpansionProgressSink | None = None,
        cancellation: CancellationToken | None = None,
    ) -> SourceExpansionResult:
        token = cancellation or CancellationToken()
        kind = classify_source_url(source)
        if kind is InputSourceKind.YOUTUBE_VIDEO:
            raise CreatorError(
                CreatorErrorCode.INVALID_SOURCE,
                "A single YouTube video does not require playlist expansion.",
            )

        if kind is InputSourceKind.YOUTUBE_PLAYLIST:
            videos = self._youtube_playlists.resolve(source, token)
            for index, video in enumerate(videos, start=1):
                token.raise_if_cancelled()
                resolved(video)
                self._report(progress, index, len(videos), f"Added {index} of {len(videos)} videos")
            return SourceExpansionResult(
                total_items=len(videos),
                resolved_items=len(videos),
                skipped_items=0,
            )

        client_id = spotify_client_id.strip()
        if not client_id:
            raise CreatorError(
                CreatorErrorCode.SPOTIFY_AUTH_REQUIRED,
                "Add a Spotify Client ID before importing a Spotify playlist.",
            )
        tracks = self._spotify_playlists.resolve(source, client_id, token)
        resolved_count = 0
        for index, track in enumerate(tracks, start=1):
            token.raise_if_cancelled()
            self._report(
                progress,
                index - 1,
                len(tracks),
                f"Finding video {index} of {len(tracks)}: {track.title}",
            )
            found_video = self._youtube_finder.find(track, token)
            if found_video is not None:
                resolved(found_video)
                resolved_count += 1
            self._report(
                progress,
                index,
                len(tracks),
                f"Matched {resolved_count} of {index} tracks",
            )
        return SourceExpansionResult(
            total_items=len(tracks),
            resolved_items=resolved_count,
            skipped_items=len(tracks) - resolved_count,
        )

    @staticmethod
    def _report(
        sink: ExpansionProgressSink | None,
        completed_items: int,
        total_items: int,
        message: str,
    ) -> None:
        if sink is not None:
            sink(
                SourceExpansionProgress(
                    completed_items=completed_items,
                    total_items=total_items,
                    message=message,
                )
            )
