"""End-to-end create-level use case."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from keywave_creator import __version__
from keywave_creator.contract.errors import ContractError
from keywave_creator.contract.package import assemble_package
from keywave_creator.domain.chart_generator import (
    ALGORITHM_VERSION,
    ChartGenerator,
    GenerationOptions,
)

from .errors import CreatorError, CreatorErrorCode
from .models import (
    AcquiredMedia,
    CancellationToken,
    CreateLevelRequest,
    CreationResult,
    ProgressStage,
    ProgressUpdate,
    SourceKind,
)
from .output import (
    PackageDestinationReservation,
    reserve_available_package_destination,
    resolved_metadata,
)
from .ports import FeatureExtractor, MediaProcessor, ProgressSink, PublicVideoDownloader

LOGGER = logging.getLogger(__name__)
MAX_DURATION_MS = 30 * 60 * 1000


class CreatorService:
    """Coordinate acquisition, normalization, analysis, generation, and packaging."""

    def __init__(
        self,
        *,
        media_processor: MediaProcessor,
        feature_extractor: FeatureExtractor,
        video_downloader: PublicVideoDownloader,
        chart_generator: ChartGenerator | None = None,
    ) -> None:
        self._media_processor = media_processor
        self._feature_extractor = feature_extractor
        self._video_downloader = video_downloader
        self._chart_generator = chart_generator or ChartGenerator()

    def create(
        self,
        request: CreateLevelRequest,
        *,
        progress: ProgressSink | None = None,
        cancellation: CancellationToken | None = None,
    ) -> CreationResult:
        token = cancellation or CancellationToken()
        self._report(progress, ProgressStage.VALIDATING, 0.02, "Validating request…")
        request.validate()
        token.raise_if_cancelled()

        with tempfile.TemporaryDirectory(prefix="keywave-creator-") as temporary:
            working_directory = Path(temporary)
            acquired = self._acquire(request, working_directory, token, progress)
            token.raise_if_cancelled()
            title, artist = resolved_metadata(
                request.title,
                request.artist,
                acquired.title,
                acquired.artist,
            )
            info = self._media_processor.probe(acquired.path, token)
            self._validate_media(info.duration_ms, info.has_audio)

            self._report(progress, ProgressStage.NORMALIZING, 0.28, "Normalizing media…")
            normalized = self._media_processor.normalize(
                acquired.path,
                info,
                working_directory,
                include_video=request.include_video,
                cancellation=token,
            )
            token.raise_if_cancelled()

            self._report(progress, ProgressStage.ANALYZING, 0.55, "Analyzing rhythm…")
            analysis = self._feature_extractor.analyze(
                normalized.analysis_path,
                expected_duration_ms=normalized.timeline.duration_ms,
                cancellation=token,
            )
            token.raise_if_cancelled()

            self._report(progress, ProgressStage.GENERATING, 0.76, "Generating four charts…")
            charts = self._chart_generator.generate(
                analysis,
                GenerationOptions(seed=request.seed),
            )
            token.raise_if_cancelled()

            self._report(progress, ProgressStage.PACKAGING, 0.90, "Writing package…")
            destination, reservation = self._destination(request, title, artist)
            try:
                try:
                    package = assemble_package(
                        destination=destination,
                        title=title,
                        artist=artist,
                        duration_ms=analysis.duration_ms,
                        charts=charts,
                        audio_path=normalized.audio_path,
                        video_path=normalized.video_path,
                        cover_path=normalized.cover_path,
                        generator_version=__version__,
                        algorithm_version=ALGORITHM_VERSION,
                        seed=request.seed,
                        source_type=request.source_kind.value,
                        source_id=acquired.source_id,
                        generation_confidence=analysis.confidence,
                        chart_offset_ms=self._microseconds_to_milliseconds(
                            normalized.timeline.sync_report.analysis_to_playback_offset_us
                        ),
                        video_offset_ms=self._microseconds_to_milliseconds(
                            normalized.timeline.video_offset_us
                        ),
                    )
                except (ContractError, OSError) as error:
                    raise CreatorError(
                        CreatorErrorCode.PACKAGE_FAILED,
                        "The generated package could not be saved.",
                        diagnostic=str(error),
                    ) from error
            finally:
                if reservation is not None:
                    reservation.release()

        self._report(progress, ProgressStage.COMPLETE, 1.0, "Package created.")
        LOGGER.info(
            "Created package %s at %s with algorithm %s",
            package.manifest.package_id,
            destination,
            ALGORITHM_VERSION,
        )
        note_counts = tuple(len(chart.notes) for chart in package.charts)
        return CreationResult(
            destination=destination,
            package_id=package.manifest.package_id,
            note_counts=(note_counts[0], note_counts[1], note_counts[2], note_counts[3]),
            bpm=analysis.bpm,
            confidence=analysis.confidence,
            title=title,
            artist=artist,
        )

    @staticmethod
    def _destination(
        request: CreateLevelRequest,
        title: str,
        artist: str,
    ) -> tuple[Path, PackageDestinationReservation | None]:
        if request.destination is not None:
            destination = request.destination.expanduser().resolve()
            normalized = (
                destination
                if destination.suffix.casefold() == ".keywave"
                else destination.with_suffix(".keywave")
            )
            return normalized, None
        if request.output_directory is None:
            raise CreatorError(
                CreatorErrorCode.PACKAGE_FAILED,
                "Choose an output folder before creating the level.",
            )
        reservation = reserve_available_package_destination(
            request.output_directory,
            title,
            artist,
        )
        return reservation.destination, reservation

    def _acquire(
        self,
        request: CreateLevelRequest,
        working_directory: Path,
        token: CancellationToken,
        progress: ProgressSink | None,
    ) -> AcquiredMedia:
        self._report(progress, ProgressStage.ACQUIRING, 0.10, "Acquiring media…")
        if request.source_kind is SourceKind.YOUTUBE:
            return self._video_downloader.download(request.source, working_directory, token)

        source = Path(request.source).expanduser().resolve()
        if not source.is_file():
            raise CreatorError(
                CreatorErrorCode.INVALID_SOURCE,
                "The selected local media file does not exist.",
            )
        return AcquiredMedia(path=source)

    @staticmethod
    def _validate_media(duration_ms: int, has_audio: bool) -> None:
        if not has_audio:
            raise CreatorError(
                CreatorErrorCode.UNSUPPORTED_MEDIA,
                "The selected media has no audio stream.",
            )
        if duration_ms <= 0 or duration_ms > MAX_DURATION_MS:
            raise CreatorError(
                CreatorErrorCode.UNSUPPORTED_MEDIA,
                "Media duration must be between 1 millisecond and 30 minutes.",
            )

    @staticmethod
    def _microseconds_to_milliseconds(value_us: int) -> int:
        return int(value_us / 1_000 + (0.5 if value_us >= 0 else -0.5))

    @staticmethod
    def _report(
        sink: ProgressSink | None,
        stage: ProgressStage,
        fraction: float,
        message: str,
    ) -> None:
        if sink is not None:
            sink(ProgressUpdate(stage=stage, fraction=fraction, message=message))
