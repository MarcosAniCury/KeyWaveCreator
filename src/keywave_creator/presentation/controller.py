"""Thread-safe QML controller for the focused YouTube creation flow."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from platformdirs import user_config_path
from PySide6.QtCore import Property, QObject, QSettings, QStandardPaths, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from keywave_creator.application.errors import CreatorError
from keywave_creator.application.models import (
    CancellationToken,
    CreateLevelRequest,
    CreationResult,
    ProgressUpdate,
    SourceKind,
)
from keywave_creator.application.service import CreatorService

LOGGER = logging.getLogger(__name__)
OUTPUT_DIRECTORY_KEY = "creator/outputDirectory"
SETTINGS_FILE_NAME = "settings.ini"


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
    progress = Signal(float, str, str)
    succeeded = Signal(str, str)
    failed = Signal(str, str)
    finished = Signal()

    def __init__(
        self,
        service_factory: Callable[[], CreatorService],
        request: CreateLevelRequest,
        cancellation: CancellationToken,
    ) -> None:
        super().__init__()
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
                "Creator operation failed with %s: %s",
                error.code,
                error.diagnostic or error.public_message,
            )
            self.failed.emit(error.code.value, error.public_message)
        except Exception:
            LOGGER.exception("Unexpected Creator worker failure")
            self.failed.emit(
                "INTERNAL_ERROR",
                "Something unexpected happened. Check the diagnostic log and try again.",
            )
        else:
            self.succeeded.emit(str(result.destination), self._summary(result))
        finally:
            self.finished.emit()

    def _report_progress(self, update: ProgressUpdate) -> None:
        self.progress.emit(update.fraction, update.message, update.stage.value)

    @staticmethod
    def _summary(result: CreationResult) -> str:
        counts = " / ".join(str(count) for count in result.note_counts)
        details = f"BPM {result.bpm:.1f}  ·  confidence {result.confidence:.0%}  ·  notes {counts}"
        if result.title and result.artist:
            return f"{result.title}  ·  {result.artist}\n{details}"
        return details


class CreatorController(QObject):
    """Expose creation state, persisted output preferences, and commands to QML."""

    runningChanged = Signal()
    progressChanged = Signal()
    stageChanged = Signal()
    statusChanged = Signal()
    errorChanged = Signal()
    resultChanged = Signal()
    outputDirectoryChanged = Signal()

    def __init__(
        self,
        service_factory: Callable[[], CreatorService],
        *,
        settings: QSettings | None = None,
        default_output_directory: Path | None = None,
    ) -> None:
        super().__init__()
        self._service_factory = service_factory
        self._settings = settings if settings is not None else _persistent_creator_settings()
        self._default_output_directory = default_output_directory or self._default_directory()
        self._output_directory = self._load_output_directory()
        self._is_running = False
        self._progress = 0.0
        self._stage = "idle"
        self._status = "Ready when you are"
        self._error = ""
        self._result_path = ""
        self._result_summary = ""
        self._thread: QThread | None = None
        self._worker: _GenerationWorker | None = None
        self._cancellation: CancellationToken | None = None

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

    @Slot(str)
    def startCreation(self, youtube_url: str) -> None:
        if self._is_running:
            return
        try:
            request = CreateLevelRequest(
                source_kind=SourceKind.YOUTUBE,
                source=youtube_url.strip(),
                output_directory=self._output_directory,
            )
            request.validate()
        except CreatorError as error:
            self._set_error(error.public_message)
            return

        self._clear_result()
        self._set_error("")
        self._set_progress(0.0)
        self._set_stage("validating")
        self._set_status("Preparing your level…")
        self._set_running(True)

        cancellation = CancellationToken()
        thread = QThread(self)
        worker = _GenerationWorker(self._service_factory, request, cancellation)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_progress)
        worker.succeeded.connect(self._on_succeeded)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_thread_finished)
        self._thread = thread
        self._worker = worker
        self._cancellation = cancellation
        thread.start()

    @Slot(str)
    def setOutputDirectory(self, value: str) -> None:
        if self._is_running:
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
        if self._is_running:
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
        if not self._is_running:
            self._clear_result()
            self._set_error("")
            self._set_progress(0.0)
            self._set_stage("idle")
            self._set_status("Ready when you are")

    @Slot()
    def cancelCreation(self) -> None:
        if self._cancellation is not None:
            self._set_status("Cancelling…")
            self._cancellation.cancel()

    @Slot()
    def shutdown(self) -> None:
        if self._cancellation is not None:
            self._cancellation.cancel()
        if self._thread is not None and self._thread.isRunning():
            self._thread.quit()
            if not self._thread.wait(5_000):
                LOGGER.error("Creator worker did not stop before application shutdown")

    @Slot(float, str, str)
    def _on_progress(self, fraction: float, message: str, stage: str) -> None:
        self._set_progress(fraction)
        self._set_stage(stage)
        self._set_status(message)

    @Slot(str, str)
    def _on_succeeded(self, destination: str, summary: str) -> None:
        self._result_path = destination
        self._result_summary = summary
        self.resultChanged.emit()
        self._set_error("")
        self._set_progress(1.0)
        self._set_stage("complete")
        self._set_status("Your level is ready")

    @Slot(str, str)
    def _on_failed(self, _code: str, message: str) -> None:
        self._set_error(message)
        self._set_status("We couldn't create this level")

    @Slot()
    def _on_thread_finished(self) -> None:
        self._thread = None
        self._worker = None
        self._cancellation = None
        self._set_running(False)

    def _load_output_directory(self) -> Path:
        stored = self._settings.value(OUTPUT_DIRECTORY_KEY, "")
        if isinstance(stored, str) and stored.strip():
            return Path(stored).expanduser().resolve()
        return self._default_output_directory

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
