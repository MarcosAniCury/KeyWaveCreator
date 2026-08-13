from __future__ import annotations

from pathlib import Path
from typing import cast

from PySide6.QtCore import QSettings, QUrl

from keywave_creator.application.models import (
    CancellationToken,
    CreateLevelRequest,
    CreationResult,
)
from keywave_creator.application.ports import ProgressSink
from keywave_creator.application.service import CreatorService
from keywave_creator.presentation.controller import (
    OUTPUT_DIRECTORY_KEY,
    SPOTIFY_CLIENT_ID_KEY,
    CreatorController,
    _persistent_creator_settings,
)


def _unused_service() -> CreatorService:
    return cast(CreatorService, object())


class _BlockingService:
    def create(
        self,
        _request: CreateLevelRequest,
        *,
        progress: ProgressSink | None = None,
        cancellation: CancellationToken | None = None,
    ) -> CreationResult:
        del progress
        assert cancellation is not None
        assert cancellation.wait(5.0)
        cancellation.raise_if_cancelled()
        raise AssertionError("Cancellation must terminate the blocking service")


def _blocking_service() -> CreatorService:
    return cast(CreatorService, _BlockingService())


def test_output_directory_is_persisted_between_controller_instances(tmp_path: Path) -> None:
    settings_path = tmp_path / "creator.ini"
    default_directory = tmp_path / "default"
    chosen_directory = tmp_path / "chosen"
    first_settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
    first = CreatorController(
        _unused_service,
        settings=first_settings,
        default_output_directory=default_directory,
    )

    assert first.outputDirectory == str(default_directory)
    first.setOutputDirectory(QUrl.fromLocalFile(str(chosen_directory)).toString())

    second = CreatorController(
        _unused_service,
        settings=QSettings(str(settings_path), QSettings.Format.IniFormat),
        default_output_directory=default_directory,
    )
    assert second.outputDirectory == str(chosen_directory.resolve())


def test_file_settings_migrate_the_previous_qt_preference_once(tmp_path: Path) -> None:
    legacy = QSettings(str(tmp_path / "legacy.ini"), QSettings.Format.IniFormat)
    legacy.setValue(OUTPUT_DIRECTORY_KEY, str(tmp_path / "legacy-output"))
    legacy.sync()

    settings = _persistent_creator_settings(
        config_directory=tmp_path / "stable",
        legacy_settings=legacy,
    )

    assert Path(settings.fileName()).resolve() == (tmp_path / "stable" / "settings.ini").resolve()
    assert settings.value(OUTPUT_DIRECTORY_KEY) == str(tmp_path / "legacy-output")

    settings.setValue(OUTPUT_DIRECTORY_KEY, str(tmp_path / "new-output"))
    settings.sync()
    legacy.setValue(OUTPUT_DIRECTORY_KEY, str(tmp_path / "changed-legacy-output"))
    legacy.sync()

    reloaded = _persistent_creator_settings(
        config_directory=tmp_path / "stable",
        legacy_settings=legacy,
    )
    assert reloaded.value(OUTPUT_DIRECTORY_KEY) == str(tmp_path / "new-output")


def test_empty_url_is_rejected_before_worker_start(tmp_path: Path) -> None:
    controller = CreatorController(
        _unused_service,
        settings=QSettings(str(tmp_path / "creator.ini"), QSettings.Format.IniFormat),
        default_output_directory=tmp_path,
    )

    controller.startCreation("   ")

    assert controller.isRunning is False
    assert controller.error == "Choose a media source."


def test_pipeline_stage_tracks_progress_and_completion(tmp_path: Path) -> None:
    controller = CreatorController(
        _unused_service,
        settings=QSettings(str(tmp_path / "creator.ini"), QSettings.Format.IniFormat),
        default_output_directory=tmp_path,
    )

    assert controller.stage == "idle"

    controller._on_progress(0.55, "Analyzing rhythm…", "analyzing")
    assert controller.stage == "analyzing"

    controller._on_succeeded(str(tmp_path / "level.keywave"), "Ready")
    assert controller.stage == "complete"


def test_youtube_queue_accepts_more_jobs_while_two_are_processing(tmp_path: Path) -> None:
    controller = CreatorController(
        _blocking_service,
        settings=QSettings(str(tmp_path / "creator.ini"), QSettings.Format.IniFormat),
        default_output_directory=tmp_path,
    )

    controller.enqueueCreation("https://www.youtube.com/watch?v=firstVideo1")
    controller.enqueueCreation("https://www.youtube.com/watch?v=secondVideo")
    controller.enqueueCreation("https://www.youtube.com/watch?v=thirdVideo3")

    assert controller.queueCount == 3
    assert controller.activeCount == 2
    assert controller.pendingCount == 1
    assert controller.hasUnfinishedJobs is True

    controller.shutdown()


def test_duplicate_unfinished_youtube_link_is_rejected(tmp_path: Path) -> None:
    controller = CreatorController(
        _blocking_service,
        settings=QSettings(str(tmp_path / "creator.ini"), QSettings.Format.IniFormat),
        default_output_directory=tmp_path,
    )
    source = "https://www.youtube.com/watch?v=duplicate01"

    controller.enqueueCreation(source)
    controller.enqueueCreation(source)

    assert controller.queueCount == 1
    assert controller.error == "This link is already waiting or being processed."

    controller.shutdown()


def test_spotify_client_id_is_stored_in_update_stable_settings(tmp_path: Path) -> None:
    settings_path = tmp_path / "creator.ini"
    controller = CreatorController(
        _unused_service,
        settings=QSettings(str(settings_path), QSettings.Format.IniFormat),
        default_output_directory=tmp_path,
    )

    controller.setSpotifyClientId("clientid1234567890")

    reloaded_settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
    assert reloaded_settings.value(SPOTIFY_CLIENT_ID_KEY) == "clientid1234567890"
    reloaded = CreatorController(
        _unused_service,
        settings=reloaded_settings,
        default_output_directory=tmp_path,
    )
    assert reloaded.spotifyClientId == "clientid1234567890"
    assert reloaded.spotifyConfigured is True


def test_resolved_playlist_videos_enter_the_bounded_generation_queue(tmp_path: Path) -> None:
    controller = CreatorController(
        _blocking_service,
        settings=QSettings(str(tmp_path / "creator.ini"), QSettings.Format.IniFormat),
        default_output_directory=tmp_path,
    )

    controller._on_source_candidate(
        "source-1",
        "https://www.youtube.com/watch?v=playlist001",
        "First",
    )
    controller._on_source_candidate(
        "source-1",
        "https://www.youtube.com/watch?v=playlist002",
        "Second",
    )

    assert controller.queueCount == 2
    assert controller.activeCount == 2
    assert {snapshot.label for snapshot in controller._queue_model.snapshots()} >= {
        "First",
        "Second",
    }

    controller.shutdown()
