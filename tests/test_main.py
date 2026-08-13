from __future__ import annotations

import json
from pathlib import Path

import pytest

from keywave_creator import __main__
from keywave_creator.application.models import (
    CreateLevelRequest,
    CreationResult,
    ProgressStage,
    ProgressUpdate,
)


def test_self_test_reports_packaged_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert __main__.main(["self-test"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_validate_reports_valid_and_missing_packages(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = (
        Path(__file__).parents[1] / "contract" / "fixtures" / "1.0" / "valid" / "synthetic.keywave"
    )

    assert __main__.main(["validate", str(fixture)]) == 0
    assert json.loads(capsys.readouterr().out)["title"] == "Synthetic Pulse"
    assert __main__.main(["validate", "missing.keywave"]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_gui_dispatches_to_presentation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("keywave_creator.presentation.run_gui", lambda: 23)
    assert __main__.main(["gui"]) == 23


def test_create_dispatches_typed_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "created.keywave"

    class FakeService:
        def create(self, request: object, *, progress: object) -> CreationResult:
            del request, progress
            return CreationResult(output, "package-id", (1, 2, 3, 4), 120.0, 0.9)

    monkeypatch.setattr(
        "keywave_creator.infrastructure.bootstrap.create_default_service", lambda: FakeService()
    )
    exit_code = __main__.main(
        [
            "create",
            "--local",
            "song.wav",
            "--title",
            "Title",
            "--artist",
            "Artist",
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["packageId"] == "package-id"
    assert payload["noteCounts"] == [1, 2, 3, 4]


def test_game_create_emits_versioned_progress_and_inferred_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "Artist - Song.wav"
    source.write_bytes(b"synthetic")
    output = tmp_path / "levels" / "Artist - Song.keywave"
    events_file = tmp_path / "job.events"
    captured_request: CreateLevelRequest | None = None

    class FakeService:
        def create(
            self,
            request: CreateLevelRequest,
            *,
            progress: object,
            cancellation: object,
        ) -> CreationResult:
            nonlocal captured_request
            captured_request = request
            assert callable(progress)
            progress(ProgressUpdate(ProgressStage.ANALYZING, 0.55, "Analyzing rhythm"))
            del cancellation
            return CreationResult(
                output,
                "package-id",
                (1, 2, 3, 4),
                120.0,
                0.9,
                "Song",
                "Artist",
            )

    monkeypatch.setattr(
        "keywave_creator.infrastructure.bootstrap.create_default_service", lambda: FakeService()
    )
    exit_code = __main__.main(
        [
            "game-create",
            "--protocol-version",
            "1",
            "--local",
            str(source),
            "--output-directory",
            str(tmp_path / "levels"),
            "--events-file",
            str(events_file),
        ]
    )

    assert exit_code == 0
    messages = [json.loads(line) for line in events_file.read_text(encoding="utf-8").splitlines()]
    assert messages[0] == {
        "type": "progress",
        "stage": "analyzing",
        "progress": 0.55,
        "message": "Analyzing rhythm",
    }
    assert messages[-1]["ok"] is True
    assert messages[-1]["output"] == str(output)
    assert captured_request is not None
    assert captured_request.title == "Song"
    assert captured_request.artist == "Artist"
    assert captured_request.output_directory == tmp_path / "levels"


def test_game_create_rejects_unknown_protocol_version(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = __main__.main(
        [
            "game-create",
            "--protocol-version",
            "99",
            "--local",
            str(tmp_path / "song.wav"),
            "--output-directory",
            str(tmp_path / "levels"),
        ]
    )

    assert exit_code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["code"] == "unsupported_protocol"
