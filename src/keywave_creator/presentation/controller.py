"""Thread-safe QML controller for URL expansion and bounded level generation."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_config_path
from PySide6.QtCore import Property, QObject, QSettings, QStandardPaths, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from keywave_creator.application.errors import CreatorError, CreatorErrorCode
from keywave_creator.application.models import (
    CancellationToken,
    CreateLevelRequest,
    CreationResult,
    InputSourceKind,
    ProgressUpdate,
    ResolvedPublicVideo,
    SourceExpansionProgress,
    SourceKind,
)
from keywave_creator.application.service import CreatorService
from keywave_creator.application.source_expansion import (
    PlaylistSourceExpander,
    classify_source_url,
    validate_spotify_client_id,
)
from keywave_creator.presentation.queue_model import CreationQueueModel, QueueJobState

LOGGER = logging.getLogger(__name__)
OUTPUT_DIRECTORY_KEY = "creator/outputDirectory"
SPOTIFY_CLIENT_ID_KEY = "creator/spotifyClientId"
SETTINGS_FILE_NAME = "settings.ini"
MAX_CONCURRENT_JOBS = 2
SHUTDOWN_TIMEOUT_SECONDS = 10.0


def _persistent_creator_settings(
    *,
    config_directory: Path | None = None,
    legacy_settings: QSettings | None = None,
) -> QSettings:
    """Return update-stable file settings, migrating the former Qt preference once."""
    directory = config_directory or user_config_path(
        "KeyWave Creator",
        "KeyWave",
        ensure_exists=True,
    )
    directory.mkdir(parents=True, exist_ok=True)
    settings = QSettings(str(directory / SETTINGS_FILE_NAME), QSettings.Format.IniFormat)
    legacy = legacy_settings if legacy_settings is not None else QSettings()
    if not settings.contains(OUTPUT_DIRECTORY_KEY) and legacy.contains(OUTPUT_DIRECTORY_KEY):
        settings.setValue(OUTPUT_DIRECTORY_KEY, legacy.value(OUTPUT_DIRECTORY_KEY))
        settings.sync()
    return settings


class _GenerationWorker(QObject):
    progress = Signal(str, float, str, str)
    succeeded = Signal(str, str, str)
    failed = Signal(str, str, str)
    finished = Signal()

    def __init__(
        self,
        job_id: str,
        service_factory: Callable[[], CreatorService],
        request: CreateLevelRequest,
        cancellation: CancellationToken,
    ) -> None:
        super().__init__()
        self._job_id = job_id
        self._service_factory = service_factory
        self._request = request
        self._cancellation = cancellation

    @Slot()
    def run(self) -> None:
        try:
            result = self._service_factory().create(
                self._request,
                progress=self._report_progress,
                cancellation=self._cancellation,
            )
        except CreatorError as error:
            LOGGER.warning(
                "Creator job %s failed with %s: %s",
                self._job_id,
                error.code,
                error.diagnostic or error.public_message,
            )
            self.failed.emit(self._job_id, error.code.value, error.public_message)
        except Exception:
            LOGGER.exception("Unexpected Creator worker failure for job %s", self._job_id)
            self.failed.emit(
                self._job_id,
                "INTERNAL_ERROR",
                "Something unexpected happened. Check the diagnostic log and try again.",
            )
        else:
            self.succeeded.emit(
                self._job_id,
                str(result.destination),
                self._summary(result),
            )
        finally:
            self.finished.emit()

    def _report_progress(self, update: ProgressUpdate) -> None:
        self.progress.emit(
            self._job_id,
            update.fraction,
            update.message,
            update.stage.value,
        )

    @staticmethod
    def _summary(result: CreationResult) -> str:
        counts = " / ".join(str(count) for count in result.note_counts)
        details = f"BPM {result.bpm:.1f} - confidence {result.confidence:.0%} - notes {counts}"
        if result.title and result.artist:
            return f"{result.title} - {result.artist}\n{details}"
        return details


class _SourceExpansionWorker(QObject):
    candidate = Signal(str, str, str)
    progress = Signal(str, float, str)
    succeeded = Signal(str, int, int, int)
    failed = Signal(str, str, str)
    finished = Signal()

    def __init__(
        self,
        job_id: str,
        source_expander_factory: Callable[[], PlaylistSourceExpander],
        source: str,
        spotify_client_id: str,
        cancellation: CancellationToken,
    ) -> None:
        super().__init__()
        self._job_id = job_id
        self._source_expander_factory = source_expander_factory
        self._source = source
        self._spotify_client_id = spotify_client_id
        self._cancellation = cancellation

    @Slot()
    def run(self) -> None:
        try:
            result = self._source_expander_factory().expand(
                self._source,
                spotify_client_id=self._spotify_client_id,
                resolved=self._on_candidate,
                progress=self._on_progress,
                cancellation=self._cancellation,
            )
        except CreatorError as error:
            LOGGER.warning(
                "Source expansion %s failed with %s: %s",
                self._job_id,
                error.code,
                error.diagnostic or error.public_message,
            )
            self.failed.emit(self._job_id, error.code.value, error.public_message)
        except Exception:
            LOGGER.exception("Unexpected source expansion failure for %s", self._job_id)
            self.failed.emit(
                self._job_id,
                "INTERNAL_ERROR",
                "The playlist could not be expanded. Check the diagnostic log and try again.",
            )
        else:
            self.succeeded.emit(
                self._job_id,
                result.total_items,
                result.resolved_items,
                result.skipped_items,
            )
        finally:
            self.finished.emit()

    def _on_candidate(self, video: ResolvedPublicVideo) -> None:
        self.candidate.emit(self._job_id, video.url, video.label)

    def _on_progress(self, update: SourceExpansionProgress) -> None:
        fraction = update.completed_items / update.total_items if update.total_items > 0 else 0.0
        self.progress.emit(self._job_id, fraction, update.message)


@dataclass(slots=True)
class _ActiveJob:
    source: str
    thread: QThread
    worker: _GenerationWorker
    cancellation: CancellationToken


@dataclass(frozen=True, slots=True)
class _PendingSourceExpansion:
    source: str
    spotify_client_id: str


@dataclass(slots=True)
class _ActiveSourceExpansion:
    source: str
    thread: QThread
    worker: _SourceExpansionWorker
    cancellation: CancellationToken


class CreatorController(QObject):
    """Expose a bounded queue, persisted output preferences, and commands to QML."""

    runningChanged = Signal()
    progressChanged = Signal()
    stageChanged = Signal()
    statusChanged = Signal()
    errorChanged = Signal()
    resultChanged = Signal()
    outputDirectoryChanged = Signal()
    spotifyClientIdChanged = Signal()
    queueChanged = Signal()

    def __init__(
        self,
        service_factory: Callable[[], CreatorService],
        *,
        source_expander_factory: Callable[[], PlaylistSourceExpander] | None = None,
        settings: QSettings | None = None,
        default_output_directory: Path | None = None,
    ) -> None:
        super().__init__()
        self._service_factory = service_factory
        self._source_expander_factory = source_expander_factory
        self._settings = settings if settings is not None else _persistent_creator_settings()
        self._default_output_directory = default_output_directory or self._default_directory()
        self._output_directory = self._load_output_directory()
        self._spotify_client_id = self._load_spotify_client_id()
        self._is_running = False
        self._progress = 0.0
        self._stage = "idle"
        self._status = "Ready when you are"
        self._error = ""
        self._result_path = ""
        self._result_summary = ""
        self._queue_model = CreationQueueModel()
        self._queue_model.changed.connect(self._on_queue_model_changed)
        self._pending_requests: dict[str, CreateLevelRequest] = {}
        self._active_jobs: dict[str, _ActiveJob] = {}
        self._pending_source_expansions: dict[str, _PendingSourceExpansion] = {}
        self._active_source_expansion: _ActiveSourceExpansion | None = None
        self._source_added_counts: dict[str, int] = {}
        self._next_job_number = 1
        self._is_shutting_down = False

    @Property(bool, notify=runningChanged)
    def isRunning(self) -> bool:
        return self._is_running

    @Property(float, notify=progressChanged)
    def progress(self) -> float:
        return self._progress

    @Property(str, notify=stageChanged)
    def stage(self) -> str:
        return self._stage

    @Property(str, notify=statusChanged)
    def status(self) -> str:
        return self._status

    @Property(str, notify=errorChanged)
    def error(self) -> str:
        return self._error

    @Property(str, notify=resultChanged)
    def resultPath(self) -> str:
        return self._result_path

    @Property(str, notify=resultChanged)
    def resultSummary(self) -> str:
        return self._result_summary

    @Property(str, notify=outputDirectoryChanged)
    def outputDirectory(self) -> str:
        return str(self._output_directory)

    @Property(str, notify=outputDirectoryChanged)
    def outputDirectoryUrl(self) -> str:
        return QUrl.fromLocalFile(str(self._output_directory)).toString()

    @Property(str, notify=spotifyClientIdChanged)
    def spotifyClientId(self) -> str:
        return self._spotify_client_id

    @Property(bool, notify=spotifyClientIdChanged)
    def spotifyConfigured(self) -> bool:
        return bool(self._spotify_client_id)

    @Property(QObject, constant=True)
    def queueModel(self) -> QObject:
        return self._queue_model

    @Property(int, notify=queueChanged)
    def queueCount(self) -> int:
        return self._queue_model.count()

    @Property(int, notify=queueChanged)
    def activeCount(self) -> int:
        return len(self._active_jobs) + int(self._active_source_expansion is not None)

    @Property(int, notify=queueChanged)
    def pendingCount(self) -> int:
        return self._queue_model.count(QueueJobState.PENDING, QueueJobState.SOURCE_PENDING)

    @Property(int, notify=queueChanged)
    def completedCount(self) -> int:
        return self._queue_model.count(QueueJobState.COMPLETED)

    @Property(bool, notify=queueChanged)
    def hasUnfinishedJobs(self) -> bool:
        return (
            self._queue_model.count(
                QueueJobState.PENDING,
                QueueJobState.SOURCE_PENDING,
                QueueJobState.EXPANDING,
                QueueJobState.RUNNING,
                QueueJobState.CANCELLING,
            )
            > 0
        )

    @Slot(str)
    def startCreation(self, source_url: str) -> None:
        """Compatibility alias that adds a supported URL to the queue."""
        self.enqueueSource(source_url)

    @Slot(str)
    def enqueueCreation(self, source_url: str) -> None:
        """Compatibility alias for older QML and integrations."""
        self.enqueueSource(source_url)

    @Slot(str)
    def enqueueSource(self, source_url: str) -> None:
        """Queue a video directly or expand a YouTube/Spotify playlist off-thread."""
        if self._is_shutting_down:
            return
        try:
            source = source_url.strip()
            source_kind = classify_source_url(source)
        except CreatorError as error:
            self._set_error(error.public_message)
            return
        if self._source_is_unfinished(source):
            self._set_error("This link is already waiting or being processed.")
            return

        if source_kind is InputSourceKind.YOUTUBE_VIDEO:
            self._enqueue_video(source, "YouTube video")
            return
        if self._source_expander_factory is None:
            self._set_error("Playlist support is unavailable in this Creator build.")
            return
        if source_kind is InputSourceKind.SPOTIFY_PLAYLIST and not self._spotify_client_id:
            self._set_error("Add your Spotify Client ID before adding a Spotify playlist.")
            return

        job_number = self._next_job_number
        self._next_job_number += 1
        job_id = f"source-{job_number}"
        label = (
            "Spotify playlist"
            if source_kind is InputSourceKind.SPOTIFY_PLAYLIST
            else "YouTube playlist"
        )
        self._pending_source_expansions[job_id] = _PendingSourceExpansion(
            source=source,
            spotify_client_id=self._spotify_client_id,
        )
        self._queue_model.append(
            job_id,
            label,
            state=QueueJobState.SOURCE_PENDING,
            stage="source_pending",
            message="Waiting to read playlist",
        )
        self._set_error("")
        self._clear_result()
        self._start_available_source_expansion()

    def _enqueue_video(self, source: str, label: str, *, report_duplicate: bool = True) -> bool:
        try:
            request = CreateLevelRequest(
                source_kind=SourceKind.YOUTUBE,
                source=source,
                output_directory=self._output_directory,
            )
            request.validate()
        except CreatorError as error:
            if report_duplicate:
                self._set_error(error.public_message)
            return False
        if self._source_is_unfinished(request.source):
            if report_duplicate:
                self._set_error("This link is already waiting or being processed.")
            return False

        job_number = self._next_job_number
        self._next_job_number += 1
        job_id = f"youtube-{job_number}"
        self._pending_requests[job_id] = request
        self._queue_model.append(job_id, label or f"YouTube video {job_number}")
        self._set_error("")
        self._clear_result()
        self._start_available_jobs()
        return True

    @Slot(str)
    def setSpotifyClientId(self, value: str) -> None:
        normalized = value.strip()
        if normalized:
            try:
                normalized = validate_spotify_client_id(normalized)
            except CreatorError as error:
                self._set_error(error.public_message)
                return
        if normalized == self._spotify_client_id:
            return
        self._spotify_client_id = normalized
        if normalized:
            self._settings.setValue(SPOTIFY_CLIENT_ID_KEY, normalized)
        else:
            self._settings.remove(SPOTIFY_CLIENT_ID_KEY)
        self._settings.sync()
        self.spotifyClientIdChanged.emit()
        self._set_error("")

    @Slot(str)
    def cancelJob(self, job_id: str) -> None:
        snapshot = self._queue_model.snapshot(job_id)
        if snapshot is None or snapshot.state.is_terminal:
            return
        if snapshot.state is QueueJobState.PENDING:
            self._pending_requests.pop(job_id, None)
            self._queue_model.update(
                job_id,
                state=QueueJobState.CANCELLED,
                stage="cancelled",
                message="Cancelled before processing",
            )
            return
        if snapshot.state is QueueJobState.SOURCE_PENDING:
            self._pending_source_expansions.pop(job_id, None)
            self._queue_model.update(
                job_id,
                state=QueueJobState.CANCELLED,
                stage="cancelled",
                message="Playlist cancelled before lookup",
            )
            return
        if snapshot.state is QueueJobState.EXPANDING:
            active_source = self._active_source_expansion
            if active_source is not None:
                self._queue_model.update(
                    job_id,
                    state=QueueJobState.CANCELLING,
                    message="Stopping playlist lookup...",
                )
                active_source.cancellation.cancel()
            return
        active = self._active_jobs.get(job_id)
        if active is not None:
            self._queue_model.update(
                job_id,
                state=QueueJobState.CANCELLING,
                message="Cancelling...",
            )
            active.cancellation.cancel()

    @Slot(str)
    def removeJob(self, job_id: str) -> None:
        snapshot = self._queue_model.snapshot(job_id)
        if snapshot is not None and snapshot.state.is_terminal:
            self._queue_model.remove(job_id)

    @Slot()
    def clearFinished(self) -> None:
        for snapshot in self._queue_model.snapshots():
            if snapshot.state.is_terminal:
                self._queue_model.remove(snapshot.job_id)

    @Slot(str)
    def openJobResult(self, job_id: str) -> None:
        snapshot = self._queue_model.snapshot(job_id)
        if snapshot is not None and snapshot.result_path:
            self._open_directory(Path(snapshot.result_path).parent)

    @Slot(str)
    def setOutputDirectory(self, value: str) -> None:
        if self.hasUnfinishedJobs:
            return
        local_value = self._local_path_or_text(value).strip()
        if not local_value:
            self._set_error("Choose a valid output folder.")
            return
        directory = Path(local_value).expanduser().resolve()
        if directory.exists() and not directory.is_dir():
            self._set_error("The selected output location is not a folder.")
            return
        if directory == self._output_directory:
            return
        self._output_directory = directory
        self._settings.setValue(OUTPUT_DIRECTORY_KEY, str(directory))
        self._settings.sync()
        self.outputDirectoryChanged.emit()
        self._set_error("")

    @Slot()
    def resetOutputDirectory(self) -> None:
        if self.hasUnfinishedJobs:
            return
        self._settings.remove(OUTPUT_DIRECTORY_KEY)
        self._settings.sync()
        self._output_directory = self._default_output_directory
        self.outputDirectoryChanged.emit()

    @Slot()
    def openOutputDirectory(self) -> None:
        self._open_directory(self._output_directory)

    @Slot()
    def openResultDirectory(self) -> None:
        if self._result_path:
            self._open_directory(Path(self._result_path).parent)

    @Slot()
    def clearResult(self) -> None:
        self._clear_result()
        self._set_error("")
        if not self.hasUnfinishedJobs:
            self._set_progress(0.0)
            self._set_stage("idle")
            self._set_status("Ready when you are")

    @Slot()
    def cancelCreation(self) -> None:
        """Cancel every unfinished queue item."""
        for snapshot in self._queue_model.snapshots():
            if not snapshot.state.is_terminal:
                self.cancelJob(snapshot.job_id)

    @Slot()
    def shutdown(self) -> None:
        self._is_shutting_down = True
        for snapshot in self._queue_model.snapshots():
            if snapshot.state in {QueueJobState.PENDING, QueueJobState.SOURCE_PENDING}:
                self.cancelJob(snapshot.job_id)
        for active in self._active_jobs.values():
            active.cancellation.cancel()
            active.thread.quit()
        active_source = self._active_source_expansion
        if active_source is not None:
            active_source.cancellation.cancel()
            active_source.thread.quit()

        deadline = time.monotonic() + SHUTDOWN_TIMEOUT_SECONDS
        for job_id, active in tuple(self._active_jobs.items()):
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1_000))
            if active.thread.isRunning() and not active.thread.wait(remaining_ms):
                LOGGER.error("Creator job %s did not stop before application shutdown", job_id)
        if active_source is not None:
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1_000))
            if active_source.thread.isRunning() and not active_source.thread.wait(remaining_ms):
                LOGGER.error("Playlist lookup did not stop before application shutdown")

    @Slot(str, float, str, str)
    def _on_job_progress(
        self,
        job_id: str,
        fraction: float,
        message: str,
        stage: str,
    ) -> None:
        self._queue_model.update(
            job_id,
            progress=fraction,
            message=message,
            stage=stage,
        )
        self._on_progress(fraction, message, stage)

    @Slot(float, str, str)
    def _on_progress(self, fraction: float, message: str, stage: str) -> None:
        """Update the compact aggregate pipeline shown above the queue."""
        self._set_progress(fraction)
        self._set_stage(stage)
        self._set_status(message)

    @Slot(str, str, str)
    def _on_job_succeeded(self, job_id: str, destination: str, summary: str) -> None:
        completed_label = summary.partition("\n")[0].strip() or "Completed YouTube level"
        self._queue_model.update(
            job_id,
            label=completed_label,
            state=QueueJobState.COMPLETED,
            progress=1.0,
            stage="complete",
            message="Level ready",
            result_path=destination,
            result_summary=summary,
            error="",
        )
        self._on_succeeded(destination, summary)

    @Slot(str, str)
    def _on_succeeded(self, destination: str, summary: str) -> None:
        self._result_path = destination
        self._result_summary = summary
        self.resultChanged.emit()
        self._set_error("")
        self._set_progress(1.0)
        self._set_stage("complete")
        self._set_status("Your level is ready")

    @Slot(str, str, str)
    def _on_job_failed(self, job_id: str, code: str, message: str) -> None:
        cancelled = code == CreatorErrorCode.CANCELLED.value
        self._queue_model.update(
            job_id,
            state=QueueJobState.CANCELLED if cancelled else QueueJobState.FAILED,
            stage="cancelled" if cancelled else "failed",
            message="Cancelled" if cancelled else "Creation failed",
            error="" if cancelled else message,
        )
        if cancelled:
            self._set_status("A queued creation was cancelled")
        else:
            self._on_failed(code, message)

    @Slot(str, str, str)
    def _on_source_candidate(self, _source_job_id: str, url: str, label: str) -> None:
        if self._enqueue_video(url, label, report_duplicate=False):
            self._source_added_counts[_source_job_id] = (
                self._source_added_counts.get(_source_job_id, 0) + 1
            )

    @Slot(str, float, str)
    def _on_source_progress(self, job_id: str, fraction: float, message: str) -> None:
        self._queue_model.update(
            job_id,
            progress=fraction,
            stage="expanding",
            message=message,
        )
        self._set_progress(fraction)
        self._set_stage("expanding")
        self._set_status(message)

    @Slot(str, int, int, int)
    def _on_source_succeeded(
        self,
        job_id: str,
        total_items: int,
        _resolved_items: int,
        skipped_items: int,
    ) -> None:
        added_items = self._source_added_counts.pop(job_id, 0)
        skipped_or_duplicate_items = total_items - added_items
        message = f"Added {added_items} of {total_items} tracks to the queue"
        if skipped_or_duplicate_items:
            detail = "had no confident match or was already queued"
            if skipped_items == skipped_or_duplicate_items:
                detail = "had no confident video match"
            message += f"; {skipped_or_duplicate_items} {detail}"
        self._queue_model.update(
            job_id,
            state=QueueJobState.COMPLETED,
            progress=1.0,
            stage="complete",
            message=message,
            result_summary=message,
        )
        self._set_status(message)

    @Slot(str, str, str)
    def _on_source_failed(self, job_id: str, code: str, message: str) -> None:
        self._source_added_counts.pop(job_id, None)
        cancelled = code == CreatorErrorCode.CANCELLED.value
        self._queue_model.update(
            job_id,
            state=QueueJobState.CANCELLED if cancelled else QueueJobState.FAILED,
            stage="cancelled" if cancelled else "failed",
            message="Playlist lookup cancelled" if cancelled else "Playlist lookup failed",
            error="" if cancelled else message,
        )
        if not cancelled:
            self._on_failed(code, message)

    @Slot(str, str)
    def _on_failed(self, _code: str, message: str) -> None:
        self._set_error(message)
        self._set_status("We couldn't create one of the queued levels")

    @Slot()
    def _on_thread_finished(self) -> None:
        sender = self.sender()
        if not isinstance(sender, QThread):
            return
        job_id_value = sender.property("jobId")
        if not isinstance(job_id_value, str):
            return
        self._active_jobs.pop(job_id_value, None)
        self.queueChanged.emit()
        if not self._is_shutting_down:
            self._start_available_jobs()
        self._set_running(bool(self._active_jobs) or self._active_source_expansion is not None)

    @Slot()
    def _on_source_thread_finished(self) -> None:
        sender = self.sender()
        active = self._active_source_expansion
        if active is None or sender is not active.thread:
            return
        self._active_source_expansion = None
        self.queueChanged.emit()
        if not self._is_shutting_down:
            self._start_available_source_expansion()
        self._set_running(bool(self._active_jobs) or self._active_source_expansion is not None)

    @Slot()
    def _on_queue_model_changed(self) -> None:
        self.queueChanged.emit()

    def _start_available_jobs(self) -> None:
        while not self._is_shutting_down and len(self._active_jobs) < MAX_CONCURRENT_JOBS:
            job_id = self._queue_model.first_pending_job_id()
            if job_id is None:
                break
            request = self._pending_requests.pop(job_id, None)
            if request is None:
                self._queue_model.update(
                    job_id,
                    state=QueueJobState.FAILED,
                    stage="failed",
                    message="Creation failed",
                    error="The queued request could not be restored.",
                )
                continue
            self._start_job(job_id, request)
        self._set_running(bool(self._active_jobs) or self._active_source_expansion is not None)

    def _start_available_source_expansion(self) -> None:
        if self._is_shutting_down or self._active_source_expansion is not None:
            return
        job_id = self._queue_model.first_pending_source_id()
        if job_id is None:
            return
        request = self._pending_source_expansions.pop(job_id, None)
        if request is None or self._source_expander_factory is None:
            self._queue_model.update(
                job_id,
                state=QueueJobState.FAILED,
                stage="failed",
                message="Playlist lookup failed",
                error="The queued playlist request could not be restored.",
            )
            self._start_available_source_expansion()
            return
        cancellation = CancellationToken()
        thread = QThread(self)
        worker = _SourceExpansionWorker(
            job_id,
            self._source_expander_factory,
            request.source,
            request.spotify_client_id,
            cancellation,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.candidate.connect(self._on_source_candidate)
        worker.progress.connect(self._on_source_progress)
        worker.succeeded.connect(self._on_source_succeeded)
        worker.failed.connect(self._on_source_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_source_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._active_source_expansion = _ActiveSourceExpansion(
            source=request.source,
            thread=thread,
            worker=worker,
            cancellation=cancellation,
        )
        self._source_added_counts[job_id] = 0
        self._queue_model.update(
            job_id,
            state=QueueJobState.EXPANDING,
            progress=0.0,
            stage="expanding",
            message="Reading playlist...",
        )
        self._set_running(True)
        self._set_status("Reading playlist...")
        thread.start()

    def _start_job(self, job_id: str, request: CreateLevelRequest) -> None:
        cancellation = CancellationToken()
        thread = QThread(self)
        thread.setProperty("jobId", job_id)
        worker = _GenerationWorker(job_id, self._service_factory, request, cancellation)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_job_progress)
        worker.succeeded.connect(self._on_job_succeeded)
        worker.failed.connect(self._on_job_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._active_jobs[job_id] = _ActiveJob(
            source=request.source,
            thread=thread,
            worker=worker,
            cancellation=cancellation,
        )
        self._queue_model.update(
            job_id,
            state=QueueJobState.RUNNING,
            progress=0.0,
            stage="validating",
            message="Preparing your level...",
        )
        self._set_progress(0.0)
        self._set_stage("validating")
        self._set_status("Preparing queued level...")
        thread.start()

    def _source_is_unfinished(self, source: str) -> bool:
        normalized = source.strip()
        pending_generation = any(
            request.source == normalized for request in self._pending_requests.values()
        )
        active_generation = any(
            active.source == normalized for active in self._active_jobs.values()
        )
        pending_expansion = any(
            request.source == normalized for request in self._pending_source_expansions.values()
        )
        active_expansion = (
            self._active_source_expansion is not None
            and self._active_source_expansion.source == normalized
        )
        return pending_generation or active_generation or pending_expansion or active_expansion

    def _load_output_directory(self) -> Path:
        stored = self._settings.value(OUTPUT_DIRECTORY_KEY, "")
        if isinstance(stored, str) and stored.strip():
            return Path(stored).expanduser().resolve()
        return self._default_output_directory

    def _load_spotify_client_id(self) -> str:
        stored = self._settings.value(SPOTIFY_CLIENT_ID_KEY, "")
        if not isinstance(stored, str) or not stored.strip():
            return ""
        try:
            return validate_spotify_client_id(stored)
        except CreatorError:
            return ""

    @staticmethod
    def _default_directory() -> Path:
        music = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.MusicLocation)
        return (Path(music) / "KeyWave Levels").resolve()

    def _open_directory(self, directory: Path) -> None:
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            LOGGER.warning("Could not create output directory: %s", error)
            self._set_error("The output folder could not be opened.")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory))):
            self._set_error("The output folder could not be opened.")

    def _clear_result(self) -> None:
        if self._result_path or self._result_summary:
            self._result_path = ""
            self._result_summary = ""
            self.resultChanged.emit()

    def _set_running(self, value: bool) -> None:
        if self._is_running != value:
            self._is_running = value
            self.runningChanged.emit()

    def _set_progress(self, value: float) -> None:
        value = min(1.0, max(0.0, value))
        if self._progress != value:
            self._progress = value
            self.progressChanged.emit()

    def _set_stage(self, value: str) -> None:
        if self._stage != value:
            self._stage = value
            self.stageChanged.emit()

    def _set_status(self, value: str) -> None:
        if self._status != value:
            self._status = value
            self.statusChanged.emit()

    def _set_error(self, value: str) -> None:
        if self._error != value:
            self._error = value
            self.errorChanged.emit()

    @staticmethod
    def _local_path_or_text(value: str) -> str:
        url = QUrl(value)
        return url.toLocalFile() if url.isLocalFile() else value
