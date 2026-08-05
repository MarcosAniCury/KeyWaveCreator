"""Production composition for the Creator application."""

from __future__ import annotations

from keywave_creator.application.service import CreatorService
from keywave_creator.domain.chart_generator import ChartGenerator

from .analysis import StreamingFeatureExtractor
from .media import FFmpegMediaProcessor
from .process import ProcessRunner, ToolLocator
from .youtube import YtDlpPublicVideoDownloader


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
