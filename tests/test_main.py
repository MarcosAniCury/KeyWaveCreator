from __future__ import annotations

import json
from pathlib import Path

import pytest

from keywave_creator import __main__
from keywave_creator.application.models import CreationResult


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
