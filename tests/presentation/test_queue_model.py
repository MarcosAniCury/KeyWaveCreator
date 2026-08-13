from __future__ import annotations

from keywave_creator.presentation.queue_model import (
    CreationQueueModel,
    QueueJobRole,
    QueueJobState,
)


def test_queue_model_exposes_typed_roles_and_updates_one_job() -> None:
    model = CreationQueueModel()
    model.append("youtube-1", "YouTube video 1")

    index = model.index(0, 0)
    assert model.rowCount() == 1
    assert model.data(index, int(QueueJobRole.JOB_ID)) == "youtube-1"
    assert model.data(index, int(QueueJobRole.LABEL)) == "YouTube video 1"
    assert model.data(index, int(QueueJobRole.STATE)) == "pending"

    assert model.update(
        "youtube-1",
        state=QueueJobState.RUNNING,
        progress=1.5,
        stage="analyzing",
        message="Analyzing rhythm...",
    )
    snapshot = model.snapshot("youtube-1")

    assert snapshot is not None
    assert snapshot.state is QueueJobState.RUNNING
    assert snapshot.progress == 1.0
    assert snapshot.stage == "analyzing"
    assert model.count(QueueJobState.RUNNING) == 1


def test_queue_model_finds_pending_jobs_in_insertion_order_and_removes_terminal_job() -> None:
    model = CreationQueueModel()
    model.append("youtube-1", "YouTube video 1")
    model.append("youtube-2", "YouTube video 2")
    model.update("youtube-1", state=QueueJobState.COMPLETED)

    assert model.first_pending_job_id() == "youtube-2"
    assert model.remove("youtube-1")
    assert model.rowCount() == 1
    assert model.snapshot("youtube-1") is None


def test_queue_model_tracks_playlist_expansion_separately_from_generation() -> None:
    model = CreationQueueModel()
    model.append(
        "source-1",
        "Spotify playlist",
        state=QueueJobState.SOURCE_PENDING,
        stage="source_pending",
        message="Waiting to read playlist",
    )

    assert model.first_pending_job_id() is None
    assert model.first_pending_source_id() == "source-1"
    assert model.count(QueueJobState.SOURCE_PENDING) == 1
