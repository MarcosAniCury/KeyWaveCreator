"""Typed QML model for the bounded Creator job queue."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import ClassVar

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
    Signal,
)

_INVALID_INDEX = QModelIndex()


class QueueJobState(StrEnum):
    """Lifecycle states exposed to the Creator queue UI."""

    PENDING = "pending"
    SOURCE_PENDING = "source_pending"
    EXPANDING = "expanding"
    RUNNING = "running"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in {self.COMPLETED, self.FAILED, self.CANCELLED}


class QueueJobRole(IntEnum):
    """Stable role identifiers used by QML delegates."""

    JOB_ID = int(Qt.ItemDataRole.UserRole) + 1
    LABEL = JOB_ID + 1
    STATE = JOB_ID + 2
    PROGRESS = JOB_ID + 3
    STAGE = JOB_ID + 4
    MESSAGE = JOB_ID + 5
    RESULT_PATH = JOB_ID + 6
    RESULT_SUMMARY = JOB_ID + 7
    ERROR = JOB_ID + 8


@dataclass(slots=True)
class _QueueJob:
    job_id: str
    label: str
    state: QueueJobState = QueueJobState.PENDING
    progress: float = 0.0
    stage: str = "pending"
    message: str = "Waiting for an available slot"
    result_path: str = ""
    result_summary: str = ""
    error: str = ""


@dataclass(frozen=True, slots=True)
class QueueJobSnapshot:
    """Immutable controller view of a queued job."""

    job_id: str
    label: str
    state: QueueJobState
    progress: float
    stage: str
    message: str
    result_path: str
    result_summary: str
    error: str


class CreationQueueModel(QAbstractListModel):
    """Own queue presentation state on the Qt GUI thread."""

    changed = Signal()

    _ROLE_NAMES: ClassVar[dict[int, QByteArray]] = {
        int(QueueJobRole.JOB_ID): QByteArray(b"jobId"),
        int(QueueJobRole.LABEL): QByteArray(b"label"),
        int(QueueJobRole.STATE): QByteArray(b"jobState"),
        int(QueueJobRole.PROGRESS): QByteArray(b"progress"),
        int(QueueJobRole.STAGE): QByteArray(b"stage"),
        int(QueueJobRole.MESSAGE): QByteArray(b"message"),
        int(QueueJobRole.RESULT_PATH): QByteArray(b"resultPath"),
        int(QueueJobRole.RESULT_SUMMARY): QByteArray(b"resultSummary"),
        int(QueueJobRole.ERROR): QByteArray(b"errorText"),
    }

    def __init__(self) -> None:
        super().__init__()
        self._jobs: list[_QueueJob] = []

    def rowCount(
        self,
        parent: QModelIndex | QPersistentModelIndex = _INVALID_INDEX,
    ) -> int:
        return 0 if parent.isValid() else len(self._jobs)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> object | None:
        if not index.isValid() or not 0 <= index.row() < len(self._jobs):
            return None
        job = self._jobs[index.row()]
        values: dict[int, object] = {
            int(QueueJobRole.JOB_ID): job.job_id,
            int(QueueJobRole.LABEL): job.label,
            int(QueueJobRole.STATE): job.state.value,
            int(QueueJobRole.PROGRESS): job.progress,
            int(QueueJobRole.STAGE): job.stage,
            int(QueueJobRole.MESSAGE): job.message,
            int(QueueJobRole.RESULT_PATH): job.result_path,
            int(QueueJobRole.RESULT_SUMMARY): job.result_summary,
            int(QueueJobRole.ERROR): job.error,
        }
        return values.get(role)

    def roleNames(self) -> dict[int, QByteArray]:
        return dict(self._ROLE_NAMES)

    def append(
        self,
        job_id: str,
        label: str,
        *,
        state: QueueJobState = QueueJobState.PENDING,
        stage: str = "pending",
        message: str = "Waiting for an available slot",
    ) -> None:
        """Append one generation or source-expansion job."""
        row = len(self._jobs)
        self.beginInsertRows(QModelIndex(), row, row)
        self._jobs.append(
            _QueueJob(
                job_id=job_id,
                label=label,
                state=state,
                stage=stage,
                message=message,
            )
        )
        self.endInsertRows()
        self.changed.emit()

    def update(
        self,
        job_id: str,
        *,
        label: str | None = None,
        state: QueueJobState | None = None,
        progress: float | None = None,
        stage: str | None = None,
        message: str | None = None,
        result_path: str | None = None,
        result_summary: str | None = None,
        error: str | None = None,
    ) -> bool:
        """Update one job and notify all queue roles for its row."""
        row = self._row_for(job_id)
        if row is None:
            return False
        job = self._jobs[row]
        if state is not None:
            job.state = state
        if label is not None:
            job.label = label
        if progress is not None:
            job.progress = min(1.0, max(0.0, progress))
        if stage is not None:
            job.stage = stage
        if message is not None:
            job.message = message
        if result_path is not None:
            job.result_path = result_path
        if result_summary is not None:
            job.result_summary = result_summary
        if error is not None:
            job.error = error
        model_index = self.index(row, 0)
        self.dataChanged.emit(model_index, model_index, list(self._ROLE_NAMES))
        self.changed.emit()
        return True

    def remove(self, job_id: str) -> bool:
        """Remove one queue job by identifier."""
        row = self._row_for(job_id)
        if row is None:
            return False
        self.beginRemoveRows(QModelIndex(), row, row)
        del self._jobs[row]
        self.endRemoveRows()
        self.changed.emit()
        return True

    def snapshot(self, job_id: str) -> QueueJobSnapshot | None:
        row = self._row_for(job_id)
        if row is None:
            return None
        job = self._jobs[row]
        return QueueJobSnapshot(
            job_id=job.job_id,
            label=job.label,
            state=job.state,
            progress=job.progress,
            stage=job.stage,
            message=job.message,
            result_path=job.result_path,
            result_summary=job.result_summary,
            error=job.error,
        )

    def snapshots(self) -> tuple[QueueJobSnapshot, ...]:
        return tuple(
            snapshot for job in self._jobs if (snapshot := self.snapshot(job.job_id)) is not None
        )

    def first_pending_job_id(self) -> str | None:
        return next(
            (job.job_id for job in self._jobs if job.state is QueueJobState.PENDING),
            None,
        )

    def first_pending_source_id(self) -> str | None:
        """Return the oldest playlist source waiting to be expanded."""
        return next(
            (job.job_id for job in self._jobs if job.state is QueueJobState.SOURCE_PENDING),
            None,
        )

    def count(self, *states: QueueJobState) -> int:
        selected = frozenset(states)
        return sum(not selected or job.state in selected for job in self._jobs)

    def _row_for(self, job_id: str) -> int | None:
        return next(
            (row for row, job in enumerate(self._jobs) if job.job_id == job_id),
            None,
        )
