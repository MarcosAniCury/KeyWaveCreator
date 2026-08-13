from __future__ import annotations

from keywave_creator.application.models import (
    CancellationToken,
    InputSourceKind,
    PlaylistTrack,
    ResolvedPublicVideo,
)
from keywave_creator.application.source_expansion import (
    PlaylistSourceExpander,
    classify_source_url,
)


class _YouTubePlaylists:
    def resolve(
        self,
        _url: str,
        _cancellation: CancellationToken,
    ) -> tuple[ResolvedPublicVideo, ...]:
        return (
            ResolvedPublicVideo("https://www.youtube.com/watch?v=video000001", "First"),
            ResolvedPublicVideo("https://www.youtube.com/watch?v=video000002", "Second"),
        )


class _SpotifyPlaylists:
    def resolve(
        self,
        _url: str,
        client_id: str,
        _cancellation: CancellationToken,
    ) -> tuple[PlaylistTrack, ...]:
        assert client_id == "client-id-1234567890"
        return (
            PlaylistTrack("Found song", "Artist", 180_000),
            PlaylistTrack("Missing song", "Artist", 190_000),
        )


class _Finder:
    def find(
        self,
        track: PlaylistTrack,
        _cancellation: CancellationToken,
    ) -> ResolvedPublicVideo | None:
        if track.title == "Found song":
            return ResolvedPublicVideo(
                "https://www.youtube.com/watch?v=video000003",
                "Artist - Found song",
            )
        return None


def _expander() -> PlaylistSourceExpander:
    return PlaylistSourceExpander(
        youtube_playlists=_YouTubePlaylists(),
        spotify_playlists=_SpotifyPlaylists(),
        youtube_finder=_Finder(),
    )


def test_source_classifier_distinguishes_video_and_both_playlist_sources() -> None:
    assert (
        classify_source_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        is InputSourceKind.YOUTUBE_VIDEO
    )
    assert (
        classify_source_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL1234567890")
        is InputSourceKind.YOUTUBE_PLAYLIST
    )
    assert (
        classify_source_url(
            "https://www.youtube.com/watch?v=QWrafQg2zNY&list=RDQWrafQg2zNY&start_radio=1"
        )
        is InputSourceKind.YOUTUBE_VIDEO
    )
    assert (
        classify_source_url("https://www.youtube.com/playlist?list=PL1234567890")
        is InputSourceKind.YOUTUBE_PLAYLIST
    )
    assert (
        classify_source_url("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M")
        is InputSourceKind.SPOTIFY_PLAYLIST
    )


def test_youtube_playlist_streams_every_video_in_order() -> None:
    emitted: list[ResolvedPublicVideo] = []

    result = _expander().expand(
        "https://www.youtube.com/playlist?list=PL1234567890",
        spotify_client_id="",
        resolved=emitted.append,
    )

    assert [video.label for video in emitted] == ["First", "Second"]
    assert result.total_items == 2
    assert result.resolved_items == 2
    assert result.skipped_items == 0


def test_spotify_playlist_searches_youtube_and_reports_unmatched_tracks() -> None:
    emitted: list[ResolvedPublicVideo] = []

    result = _expander().expand(
        "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M",
        spotify_client_id="client-id-1234567890",
        resolved=emitted.append,
    )

    assert [video.label for video in emitted] == ["Artist - Found song"]
    assert result.total_items == 2
    assert result.resolved_items == 1
    assert result.skipped_items == 1
