"""Typed models and cross-document invariants for `.keywave` v1."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar, Never

from .errors import ContractError, ErrorCode, ValidationIssue

FORMAT_VERSION = "1.0.0"
LANE_COUNT = 6
MAX_DURATION_MS = 30 * 60 * 1000
MAX_NOTES_PER_CHART = 100_000


class Difficulty(StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    EXTREME = "extreme"


DIFFICULTY_ORDER: tuple[Difficulty, ...] = (
    Difficulty.EASY,
    Difficulty.MEDIUM,
    Difficulty.HARD,
    Difficulty.EXTREME,
)

ACTIVE_LANES: dict[Difficulty, tuple[int, ...]] = {
    Difficulty.EASY: (0, 1, 4, 5),
    Difficulty.MEDIUM: (0, 1, 3, 4, 5),
    Difficulty.HARD: (0, 1, 3, 4, 5),
    Difficulty.EXTREME: (0, 1, 2, 3, 4, 5),
}


class NoteType(StrEnum):
    TAP = "tap"
    HOLD = "hold"


def _fail(code: ErrorCode, message: str, pointer: str | None = None) -> Never:
    raise ContractError(ValidationIssue(code=code, message=message, pointer=pointer))


@dataclass(frozen=True, slots=True, order=True)
class Note:
    time_ms: int
    lane: int
    id: str
    type: NoteType = NoteType.TAP
    duration_ms: int | None = None

    def validate(self, *, duration_ms: int, active_lanes: tuple[int, ...]) -> None:
        if not self.id or len(self.id) > 128 or not self.id.isascii():
            _fail(ErrorCode.INVALID_CHART, "Note id must be non-empty ASCII up to 128 chars")
        if self.time_ms < 0 or self.time_ms > duration_ms:
            _fail(ErrorCode.INVALID_CHART, "Note time is outside the song")
        if self.lane not in active_lanes:
            _fail(ErrorCode.INVALID_CHART, "Note targets an inactive lane")
        if self.type is NoteType.TAP and self.duration_ms is not None:
            _fail(ErrorCode.INVALID_CHART, "Tap notes cannot have durationMs")
        if self.type is NoteType.HOLD:
            hold_duration_ms = self.duration_ms
            if hold_duration_ms is None or hold_duration_ms <= 0:
                _fail(ErrorCode.INVALID_CHART, "Hold notes require a positive durationMs")
            if self.time_ms + hold_duration_ms > duration_ms:
                _fail(ErrorCode.INVALID_CHART, "Hold extends past the song")

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "id": self.id,
            "timeMs": self.time_ms,
            "lane": self.lane,
            "type": self.type.value,
        }
        if self.duration_ms is not None:
            value["durationMs"] = self.duration_ms
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Note:
        return cls(
            id=str(value["id"]),
            time_ms=int(value["timeMs"]),
            lane=int(value["lane"]),
            type=NoteType(value["type"]),
            duration_ms=(int(value["durationMs"]) if "durationMs" in value else None),
        )


@dataclass(frozen=True, slots=True)
class Chart:
    chart_id: str
    difficulty: Difficulty
    notes: tuple[Note, ...]
    active_lanes: tuple[int, ...] = field(default_factory=tuple)
    format_version: str = FORMAT_VERSION
    lane_count: int = LANE_COUNT

    def __post_init__(self) -> None:
        if not self.active_lanes:
            object.__setattr__(self, "active_lanes", ACTIVE_LANES[self.difficulty])

    def validate(self, *, duration_ms: int) -> None:
        if self.format_version.split(".", 1)[0] != "1":
            _fail(ErrorCode.UNSUPPORTED_VERSION, "Unsupported chart major version")
        if self.lane_count != LANE_COUNT:
            _fail(ErrorCode.INVALID_CHART, "laneCount must be 6")
        if self.active_lanes != ACTIVE_LANES[self.difficulty]:
            _fail(ErrorCode.INVALID_CHART, "activeLanes do not match the difficulty profile")
        if len(self.notes) > MAX_NOTES_PER_CHART:
            _fail(ErrorCode.LIMIT_EXCEEDED, "Chart has too many notes")
        expected = tuple(sorted(self.notes, key=lambda n: (n.time_ms, n.lane, n.id)))
        if self.notes != expected:
            _fail(ErrorCode.INVALID_CHART, "Notes are not canonically sorted")
        ids: set[str] = set()
        slots: set[tuple[int, int]] = set()
        lane_ends = [0] * LANE_COUNT
        for note in self.notes:
            note.validate(duration_ms=duration_ms, active_lanes=self.active_lanes)
            if note.id in ids:
                _fail(ErrorCode.INVALID_CHART, "Duplicate note id")
            slot = (note.time_ms, note.lane)
            if slot in slots:
                _fail(ErrorCode.INVALID_CHART, "Duplicate note in one lane and time")
            if note.time_ms < lane_ends[note.lane]:
                _fail(ErrorCode.INVALID_CHART, "Notes overlap in the same lane")
            ids.add(note.id)
            slots.add(slot)
            lane_ends[note.lane] = note.time_ms + (note.duration_ms or 0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "formatVersion": self.format_version,
            "chartId": self.chart_id,
            "difficulty": self.difficulty.value,
            "laneCount": self.lane_count,
            "activeLanes": list(self.active_lanes),
            "notes": [note.to_dict() for note in self.notes],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Chart:
        return cls(
            format_version=str(value["formatVersion"]),
            chart_id=str(value["chartId"]),
            difficulty=Difficulty(value["difficulty"]),
            lane_count=int(value["laneCount"]),
            active_lanes=tuple(int(lane) for lane in value["activeLanes"]),
            notes=tuple(Note.from_dict(item) for item in value["notes"]),
        )


@dataclass(frozen=True, slots=True)
class FileDescriptor:
    path: str
    size_bytes: int
    sha256: str
    media_type: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sizeBytes": self.size_bytes,
            "sha256": self.sha256,
            "mediaType": self.media_type,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> FileDescriptor:
        return cls(
            path=str(value["path"]),
            size_bytes=int(value["sizeBytes"]),
            sha256=str(value["sha256"]),
            media_type=str(value["mediaType"]),
        )


@dataclass(frozen=True, slots=True)
class ChartDescriptor(FileDescriptor):
    difficulty: Difficulty
    chart_id: str
    note_count: int
    active_lanes: tuple[int, ...]

    def to_dict(self) -> dict[str, Any]:
        value = FileDescriptor.to_dict(self)
        value.update(
            {
                "difficulty": self.difficulty.value,
                "chartId": self.chart_id,
                "noteCount": self.note_count,
                "activeLanes": list(self.active_lanes),
            }
        )
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ChartDescriptor:
        return cls(
            path=str(value["path"]),
            size_bytes=int(value["sizeBytes"]),
            sha256=str(value["sha256"]),
            media_type=str(value["mediaType"]),
            difficulty=Difficulty(value["difficulty"]),
            chart_id=str(value["chartId"]),
            note_count=int(value["noteCount"]),
            active_lanes=tuple(int(lane) for lane in value["activeLanes"]),
        )


@dataclass(frozen=True, slots=True)
class Manifest:
    package_id: str
    title: str
    artist: str
    duration_ms: int
    generator_version: str
    algorithm_version: str
    seed: str
    audio: FileDescriptor
    charts: tuple[ChartDescriptor, ...]
    video: FileDescriptor | None = None
    cover: FileDescriptor | None = None
    source_type: str = "local"
    source_id: str | None = None
    generation_confidence: float = 1.0
    chart_offset_ms: int = 0
    video_offset_ms: int = 0
    required_features: tuple[str, ...] = ("holdNotes",)
    format_version: str = FORMAT_VERSION

    SUPPORTED_FEATURES: ClassVar[frozenset[str]] = frozenset({"holdNotes"})

    def validate(self) -> None:
        if self.format_version.split(".", 1)[0] != "1":
            _fail(ErrorCode.UNSUPPORTED_VERSION, "Unsupported package major version")
        unknown = set(self.required_features) - self.SUPPORTED_FEATURES
        if unknown:
            _fail(ErrorCode.UNSUPPORTED_FEATURE, f"Unsupported features: {sorted(unknown)}")
        if not self.package_id or not self.title.strip() or not self.artist.strip():
            _fail(ErrorCode.INVALID_MANIFEST, "Package id, title, and artist are required")
        if self.duration_ms <= 0 or self.duration_ms > MAX_DURATION_MS:
            _fail(ErrorCode.LIMIT_EXCEEDED, "Song duration must be between 1 ms and 30 minutes")
        if not 0.0 <= self.generation_confidence <= 1.0:
            _fail(ErrorCode.INVALID_MANIFEST, "generationConfidence must be within 0..1")
        if tuple(item.difficulty for item in self.charts) != DIFFICULTY_ORDER:
            _fail(ErrorCode.INVALID_MANIFEST, "Exactly four ordered difficulties are required")

    def to_dict(self) -> dict[str, Any]:
        media: dict[str, Any] = {"audio": self.audio.to_dict()}
        if self.video is not None:
            media["video"] = self.video.to_dict()
        if self.cover is not None:
            media["cover"] = self.cover.to_dict()
        source: dict[str, Any] = {"type": self.source_type}
        if self.source_id:
            source["id"] = self.source_id
        return {
            "formatVersion": self.format_version,
            "packageId": self.package_id,
            "requiredFeatures": list(self.required_features),
            "title": self.title,
            "artist": self.artist,
            "durationMs": self.duration_ms,
            "source": source,
            "generator": {
                "version": self.generator_version,
                "algorithmVersion": self.algorithm_version,
                "seed": self.seed,
                "confidence": self.generation_confidence,
            },
            "sync": {
                "chartOffsetMs": self.chart_offset_ms,
                "videoOffsetMs": self.video_offset_ms,
            },
            "media": media,
            "charts": [chart.to_dict() for chart in self.charts],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Manifest:
        media = value["media"]
        generator = value["generator"]
        source = value["source"]
        sync = value["sync"]
        return cls(
            format_version=str(value["formatVersion"]),
            package_id=str(value["packageId"]),
            required_features=tuple(str(item) for item in value["requiredFeatures"]),
            title=str(value["title"]),
            artist=str(value["artist"]),
            duration_ms=int(value["durationMs"]),
            source_type=str(source["type"]),
            source_id=(str(source["id"]) if "id" in source else None),
            generator_version=str(generator["version"]),
            algorithm_version=str(generator["algorithmVersion"]),
            seed=str(generator["seed"]),
            generation_confidence=float(generator["confidence"]),
            chart_offset_ms=int(sync["chartOffsetMs"]),
            video_offset_ms=int(sync["videoOffsetMs"]),
            audio=FileDescriptor.from_dict(media["audio"]),
            video=(FileDescriptor.from_dict(media["video"]) if "video" in media else None),
            cover=(FileDescriptor.from_dict(media["cover"]) if "cover" in media else None),
            charts=tuple(ChartDescriptor.from_dict(item) for item in value["charts"]),
        )


def validate_nested_charts(charts: tuple[Chart, ...]) -> None:
    if tuple(chart.difficulty for chart in charts) != DIFFICULTY_ORDER:
        _fail(ErrorCode.INVALID_CHART, "Charts must be easy, medium, hard, extreme")
    previous: dict[str, Note] = {}
    for chart in charts:
        current = {note.id: note for note in chart.notes}
        for note_id, lower_note in previous.items():
            if current.get(note_id) != lower_note:
                _fail(ErrorCode.INVALID_CHART, "Lower-difficulty notes must be preserved")
        previous = current
