"""Production composition for the Creator application."""

from __future__ import annotations

from keywave_creator.application.service import CreatorService
from keywave_creator.application.source_expansion import PlaylistSourceExpander
from keywave_creator.domain.chart_generator import ChartGenerator

from .analysis import StreamingFeatureExtractor
from .media import FFmpegMediaProcessor
from .process import ProcessRunner, ToolLocator
from .spotify import SpotifyPkceSession, SpotifyWebApiPlaylistResolver
from .youtube import (
    YtDlpPublicVideoDownloader,
    YtDlpYouTubeMusicVideoFinder,
    YtDlpYouTubePlaylistResolver,
)


def create_default_service() -> CreatorService:
    """Build the production service with shared process/tool infrastructure."""
    runner = ProcessRunner()
    locator = ToolLocator()
    return CreatorService(
        media_processor=FFmpegMediaProcessor(runner=runner, locator=locator),
        feature_extractor=StreamingFeatureExtractor(),
        video_downloader=YtDlpPublicVideoDownloader(runner=runner, locator=locator),
        chart_generator=ChartGenerator(),
    )


def create_default_source_expander(
    spotify_session: SpotifyPkceSession,
) -> PlaylistSourceExpander:
    """Build playlist discovery adapters around one in-memory Spotify session."""
    runner = ProcessRunner()
    locator = ToolLocator()
    return PlaylistSourceExpander(
        youtube_playlists=YtDlpYouTubePlaylistResolver(runner=runner, locator=locator),
        spotify_playlists=SpotifyWebApiPlaylistResolver(session=spotify_session),
        youtube_finder=YtDlpYouTubeMusicVideoFinder(runner=runner, locator=locator),
    )
